import json
import logging
import os
import random
from datetime import date, datetime, time, timedelta

from odoo import api, fields, models
from odoo.fields import Command

_logger = logging.getLogger(__name__)

COMPANY_NAME = "Summit Office Supply Co."
PERIOD_START = date(2026, 7, 1)
LAST_ORDER_DATE = date(2026, 9, 26)
LAST_PAYMENT_DATE = date(2026, 9, 28)
CLOSE_DATE = date(2026, 9, 30)
BACKGROUND_ORDERS = 150
EVIDENCE_DIR_ENV = 'MONTH_END_EVIDENCE_DIR'
DEFAULT_EVIDENCE_DIR = os.path.expanduser('~/.local/share/month_end_close')

HARBOR = "Harbor Coffee Roasters"
BLUEBIRD = "Bluebird Bakery"
MAPLE = "Maple Street Dental"
MAPLE_DUPLICATE = "Maple St. Dental"
GREENLEAF = "Greenleaf Architects"
RIVERSIDE = "Riverside Yoga Studio"
UNKNOWN_SENDER = "J. PATEL HOLDINGS"
CHAIR = "Ergonomic Mesh Office Chair"
DISPENSER = "Countertop Water Dispenser"


def evidence_path(dbname):
    directory = os.environ.get(EVIDENCE_DIR_ENV) or DEFAULT_EVIDENCE_DIR
    return os.path.join(directory, f'{dbname}-evidence.json')


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _build_month_end_close_world(self, seed):
        rng = random.Random(seed)
        company = self._mec_setup_company()
        customers = self.env['res.partner'].search([('customer_rank', '>', 0), ('email', '=like', 'accounts@%')], order='id')
        products = self.env['product.product'].search([('default_code', '=like', 'SOS-%')], order='default_code')
        products.product_tmpl_id.write({'taxes_id': [Command.clear()]})
        by_name = {partner.name: partner for partner in customers}
        by_name[MAPLE_DUPLICATE] = self._mec_create_duplicate(by_name[MAPLE])
        product_by_name = {product.name: product for product in products}

        plans = self._mec_background_plans(rng, customers, products) + self._mec_cast_plans(by_name, product_by_name)
        invoices = {}
        payment_events = []
        for index, plan in enumerate(sorted(plans, key=lambda plan: plan['date'])):
            order = self._mec_create_order(plan['partner'], plan['date'], plan['lines'])
            invoice = self._mec_create_invoice(order, plan['date'], plan.get('price_override'))
            if plan.get('tag'):
                invoices[plan['tag']] = invoice
            if plan.get('pay_date'):
                payment_events.append((plan['pay_date'], index, invoice))
        for pay_date, _index, invoice in sorted(payment_events, key=lambda event: event[:2]):
            self._mec_register_payment(invoice, pay_date)

        defects = self._mec_plant_defects(by_name, invoices)
        evidence = {
            'environment': 'month_end_close',
            'company': company.name,
            'period': 'September 2026',
            'close_date': CLOSE_DATE.isoformat(),
            'builder_seed': seed,
            'built_at': fields.Datetime.to_string(fields.Datetime.now()),
            'defects': defects,
            'baseline': self._mec_baseline(defects),
        }
        path = evidence_path(self.env.cr.dbname)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(evidence, handle, indent=2)
        _logger.info("Month-end close world built: %d defects, evidence written to %s", len(defects), path)

    # ------------------------------------------------------------------ setup

    def _mec_setup_company(self):
        company = self.env.company
        company.write({
            'name': COMPANY_NAME,
            'street': '1200 SW Industrial Way',
            'city': 'Portland',
            'zip': '97201',
            'country_id': self.env.ref('base.us').id,
            'email': 'ar@summitofficesupply.com',
            'phone': '(555) 010-2026',
            'account_sale_tax_id': False,
        })
        chart = self.env['account.chart.template']
        bank = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1)
        bank.inbound_payment_method_line_ids.payment_account_id = chart.ref('account_journal_payment_debit_account_id')
        bank.outbound_payment_method_line_ids.payment_account_id = chart.ref('account_journal_payment_credit_account_id')
        return company

    def _mec_create_duplicate(self, original):
        return self.env['res.partner'].create({
            'name': MAPLE_DUPLICATE,
            'customer_rank': 1,
            'street': original.street,
            'city': original.city,
            'zip': original.zip,
            'country_id': original.country_id.id,
            'phone': original.phone,
            'email': 'billing@maplestreetdental.com',
        })

    # ------------------------------------------------------------------ plans

    def _mec_background_plans(self, rng, customers, products):
        plans = []
        for _ in range(BACKGROUND_ORDERS):
            order_date = PERIOD_START + timedelta(days=rng.randint(0, (LAST_ORDER_DATE - PERIOD_START).days))
            partner = rng.choice(customers)
            lines = [
                (rng.choice(products), rng.choice([1, 2, 3, 4, 5, 6, 8, 10, 12, 20]), None)
                for _ in range(rng.randint(1, 4))
            ]
            age = (LAST_PAYMENT_DATE - order_date).days
            paid = rng.random() < (0.85 if age > 35 else 0.45)
            pay_date = order_date + timedelta(days=rng.randint(5, 32))
            plans.append({
                'date': order_date,
                'partner': partner,
                'lines': lines,
                'pay_date': pay_date if paid and pay_date <= LAST_PAYMENT_DATE else None,
            })
        return plans

    def _mec_cast_plans(self, by_name, product_by_name):
        def lines(*specs):
            return [(product_by_name[name], qty, price) for name, qty, price in specs]

        basket = lines(("Copy Paper, Letter (case)", 3, None), ("Printer Toner, Black", 1, None),
                       ("Sticky Notes (12 pack)", 4, None))
        plans = [
            # Ordinary paid history for every defect customer, so they don't stand out.
            {'date': day, 'partner': by_name[name], 'lines': basket, 'pay_date': day + timedelta(days=21)}
            for name, day in ((HARBOR, date(2026, 7, 14)), (BLUEBIRD, date(2026, 7, 22)), (MAPLE, date(2026, 7, 9)),
                              (GREENLEAF, date(2026, 7, 30)), (RIVERSIDE, date(2026, 8, 5)))
        ]
        plans += [
            {'tag': 'harbor', 'date': date(2026, 8, 18), 'partner': by_name[HARBOR], 'lines': lines(
                ("Coffee Beans, House Blend (5 lb)", 12, 64.0), ("Paper Cups, 12 oz (1000)", 6, 89.0),
                ("Coffee Filters (case)", 4, 38.5))},
            {'tag': 'maple', 'date': date(2026, 8, 27), 'partner': by_name[MAPLE_DUPLICATE], 'lines': lines(
                ("First Aid Kit, Office", 4, 55.0), ("Disinfecting Wipes (6 pack)", 20, 24.0),
                ("Copy Paper, Letter (case)", 8, 46.0))},
            {'tag': 'greenleaf', 'date': date(2026, 9, 2), 'partner': by_name[GREENLEAF],
             'lines': lines((CHAIR, 20, 180.0)), 'price_override': {CHAIR: 225.0}},
            {'tag': 'riverside', 'date': date(2026, 9, 4), 'partner': by_name[RIVERSIDE], 'lines': lines(
                (DISPENSER, 6, 150.0), ("Water Filter Cartridge", 12, 22.0))},
            {'tag': 'bluebird', 'date': date(2026, 9, 10), 'partner': by_name[BLUEBIRD], 'lines': lines(
                ("Paper Towels (case)", 10, 42.0), ("Hand Soap Refill (gallon)", 5, 27.0))},
        ]
        return plans

    # --------------------------------------------------------------- workflow

    def _mec_create_order(self, partner, order_date, lines):
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'payment_term_id': self.env.ref('account.account_payment_term_30days').id,
            'order_line': [
                Command.create({
                    'product_id': product.id,
                    'product_uom_qty': qty,
                    **({'price_unit': price} if price is not None else {}),
                })
                for product, qty, price in lines
            ],
        })
        order.action_confirm()
        order_dt = datetime.combine(order_date, time(10, 0))
        order.write({'date_order': order_dt, 'validity_date': order_date + timedelta(days=30)})
        for picking in order.picking_ids:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.button_validate()
            picking.write({'date_done': order_dt + timedelta(days=1), 'scheduled_date': order_dt + timedelta(days=1)})
        return order

    def _mec_create_invoice(self, order, invoice_date, price_override=None):
        invoice = order._create_invoices()
        invoice.write({'invoice_date': invoice_date})
        for product_name, price in (price_override or {}).items():
            invoice.invoice_line_ids.filtered(lambda line: line.product_id.name == product_name).price_unit = price
        invoice.action_post()
        return invoice

    def _mec_register_payment(self, invoice, pay_date, amount=None, memo=None):
        wizard_vals = {'payment_date': pay_date}
        if amount is not None:
            wizard_vals.update({'amount': amount, 'payment_difference_handling': 'open'})
        if memo:
            wizard_vals['communication'] = memo
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create(wizard_vals)
        return wizard._create_payments()

    def _mec_unapplied_payment(self, partner, amount, pay_date, memo):
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': partner.id if partner else False,
            'amount': amount,
            'date': pay_date,
            'memo': memo,
            'journal_id': self.env['account.journal'].search([('type', '=', 'bank')], limit=1).id,
        })
        payment.action_post()
        return payment

    # ---------------------------------------------------------------- defects

    def _mec_plant_defects(self, by_name, invoices):
        harbor_invoice, bluebird_invoice = invoices['harbor'], invoices['bluebird']
        maple_invoice, greenleaf_invoice, riverside_invoice = invoices['maple'], invoices['greenleaf'], invoices['riverside']
        greenleaf_order = greenleaf_invoice.invoice_line_ids.sale_line_ids.order_id
        overcharge = greenleaf_invoice.amount_total - greenleaf_order.amount_total
        short_amount = 450.0
        unknown_amount = 1847.30

        events = sorted([
            (date(2026, 9, 12), 'D1', lambda: self._mec_unapplied_payment(
                by_name[BLUEBIRD], harbor_invoice.amount_total, date(2026, 9, 12),
                f"{HARBOR} - payment for {harbor_invoice.name}")),
            (date(2026, 9, 16), 'D2', lambda: self._mec_unapplied_payment(
                by_name[MAPLE], maple_invoice.amount_total, date(2026, 9, 16), f"{MAPLE} - {maple_invoice.name}")),
            (date(2026, 9, 19), 'D3', lambda: self._mec_register_payment(
                greenleaf_invoice, date(2026, 9, 19), amount=greenleaf_order.amount_total,
                memo=f"{greenleaf_order.name} - 20 chairs at the quoted $180")),
            (date(2026, 9, 22), 'D4', lambda: self._mec_unapplied_payment(
                False, unknown_amount, date(2026, 9, 22), f"Incoming wire from {UNKNOWN_SENDER} - no invoice reference")),
            (date(2026, 9, 24), 'D5', lambda: self._mec_unapplied_payment(
                by_name[RIVERSIDE], riverside_invoice.amount_total - short_amount, date(2026, 9, 24),
                f"{riverside_invoice.name} - short-paid $450 for 3 damaged water dispensers, disputing")),
        ], key=lambda event: event[0])
        payments = {defect_id: create() for _day, defect_id, create in events}

        return [
            {
                'id': 'D1',
                'key': 'misfiled_payment',
                'title': "Payment filed under the wrong customer",
                'story': (f"{HARBOR} paid {harbor_invoice.name} (${harbor_invoice.amount_total:,.2f}), but the payment "
                          f"was recorded under {BLUEBIRD}. Harbor's invoice looks unpaid; Bluebird shows a credit it "
                          f"doesn't own."),
                'trap': f"{BLUEBIRD} has its own open invoice {bluebird_invoice.name}; applying the credit there is wrong.",
                'expected': f"Move the payment to {HARBOR} and apply it to {harbor_invoice.name}. Leave {bluebird_invoice.name} open.",
                'fixable_in_product': True,
                'records': {
                    'invoice_id': harbor_invoice.id, 'invoice': harbor_invoice.name,
                    'payment_id': payments['D1'].id, 'payment': payments['D1'].name,
                    'right_partner_id': by_name[HARBOR].id, 'wrong_partner_id': by_name[BLUEBIRD].id,
                    'trap_invoice_id': bluebird_invoice.id, 'trap_invoice': bluebird_invoice.name,
                    'amount': harbor_invoice.amount_total,
                },
            },
            {
                'id': 'D2',
                'key': 'duplicate_customer',
                'title': "Same customer exists twice",
                'story': (f"'{MAPLE}' and '{MAPLE_DUPLICATE}' are the same office (same address and phone). "
                          f"{maple_invoice.name} (${maple_invoice.amount_total:,.2f}) was billed to the duplicate "
                          f"and their payment landed on the original, so neither side reconciles."),
                'expected': f"Merge the two contacts, then apply the payment to {maple_invoice.name}.",
                'fixable_in_product': True,
                'records': {
                    'invoice_id': maple_invoice.id, 'invoice': maple_invoice.name,
                    'payment_id': payments['D2'].id, 'payment': payments['D2'].name,
                    'original_partner_id': by_name[MAPLE].id, 'duplicate_partner_id': by_name[MAPLE_DUPLICATE].id,
                    'amount': maple_invoice.amount_total,
                },
            },
            {
                'id': 'D3',
                'key': 'overbilled_invoice',
                'title': "Invoice doesn't match the sales order",
                'story': (f"{GREENLEAF} ordered 20 chairs at $180 on {greenleaf_order.name} "
                          f"(${greenleaf_order.amount_total:,.2f}). {greenleaf_invoice.name} billed them at $225 "
                          f"(${greenleaf_invoice.amount_total:,.2f}). They paid the order price, leaving "
                          f"${overcharge:,.2f} stuck open."),
                'expected': (f"Our billing error: credit the ${overcharge:,.2f} overcharge so the net billed equals "
                             f"the order and the invoice closes."),
                'fixable_in_product': True,
                'records': {
                    'order_id': greenleaf_order.id, 'order': greenleaf_order.name,
                    'invoice_id': greenleaf_invoice.id, 'invoice': greenleaf_invoice.name,
                    'payment_id': payments['D3'].id, 'payment': payments['D3'].name,
                    'order_total': greenleaf_order.amount_total, 'invoice_total': greenleaf_invoice.amount_total,
                },
            },
            {
                'id': 'D4',
                'key': 'unidentified_wire',
                'title': "Money from an unknown sender",
                'story': (f"A ${unknown_amount:,.2f} wire arrived from '{UNKNOWN_SENDER}'. There is no such customer "
                          f"and no open invoice for that amount."),
                'expected': "Don't force-match it. Leave it unapplied and log it as an exception on the payment.",
                'fixable_in_product': False,
                'records': {'payment_id': payments['D4'].id, 'payment': payments['D4'].name, 'amount': unknown_amount},
            },
            {
                'id': 'D5',
                'key': 'disputed_short_payment',
                'title': "Customer short-paid and is disputing",
                'story': (f"{RIVERSIDE} paid ${riverside_invoice.amount_total - short_amount:,.2f} against "
                          f"{riverside_invoice.name} (${riverside_invoice.amount_total:,.2f}), holding back $450 for "
                          f"3 water dispensers they say arrived damaged."),
                'expected': (f"Apply the payment to {riverside_invoice.name} and leave $450 open. Don't credit or "
                             f"write off a disputed amount; log it as an exception."),
                'fixable_in_product': False,
                'records': {
                    'invoice_id': riverside_invoice.id, 'invoice': riverside_invoice.name,
                    'payment_id': payments['D5'].id, 'payment': payments['D5'].name,
                    'short_amount': short_amount,
                },
            },
        ]

    def _mec_baseline(self, defects):
        defect_invoice_ids = [defect['records']['invoice_id'] for defect in defects if 'invoice_id' in defect['records']]
        invoices = self.env['account.move'].search([
            ('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), ('id', 'not in', defect_invoice_ids),
        ], order='id')
        return {
            'invoices': {
                str(invoice.id): {
                    'name': invoice.name,
                    'partner': invoice.partner_id.name,
                    'amount_residual': invoice.amount_residual,
                    'payment_state': invoice.payment_state,
                }
                for invoice in invoices
            },
        }
