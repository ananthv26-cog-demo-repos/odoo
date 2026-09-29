#!/usr/bin/env python3
# ruff: noqa: T201
"""Grade a month-end close run against the evidence set written at seed time.

    verify.py --db mec1 [--url http://localhost:8069] [--html scorecard.html]
    verify.py --db mec1 --answer-key answer_key.html
    verify.py --db mec1 --dump world.json

Only the standard library is used, so this runs anywhere that can reach Odoo over XML-RPC.
"""
import argparse
import html
import json
import os
import sys
import xmlrpc.client
from datetime import datetime

TOLERANCE = 0.01


class Odoo:
    def __init__(self, url, db, login, password):
        self.db, self.password = db, password
        common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
        self.uid = common.authenticate(db, login, password, {})
        if not self.uid:
            sys.exit(f"Could not log in to {url} db={db} as {login}")
        self.models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

    def call(self, model, method, *args, **kwargs):
        return self.models.execute_kw(self.db, self.uid, self.password, model, method, list(args), kwargs)

    def read(self, model, ids, fields):
        return {rec['id']: rec for rec in self.call(model, 'read', ids, fields, context={'active_test': False})}

    def search_read(self, model, domain, fields, order='id'):
        return self.call(model, 'search_read', domain, fields, order=order, context={'active_test': False})

    def exists(self, model, ids):
        return set(self.call(model, 'search', [('id', 'in', ids)], context={'active_test': False}))


def m2o_id(value):
    return value[0] if value else False


def close(a, b):
    return abs(a - b) <= TOLERANCE


def has_note_since(odoo, model_ids, since):
    domain = ['|'] * (len(model_ids) - 1)
    for model, res_id in model_ids:
        domain += ['&', ('model', '=', model), ('res_id', '=', res_id)]
    notes = odoo.search_read('mail.message', [('message_type', '=', 'comment'), ('date', '>', since)] + domain,
                             ['body', 'model', 'res_id'])
    return any(note['body'] for note in notes)


class Grader:
    def __init__(self, odoo, evidence):
        self.odoo = odoo
        self.evidence = evidence
        self.since = evidence['built_at']

    def payment(self, payment_id):
        found = self.odoo.read('account.payment', [payment_id],
                               ['name', 'state', 'partner_id', 'amount', 'reconciled_invoice_ids', 'move_id'])
        return found.get(payment_id)

    def invoice(self, invoice_id):
        found = self.odoo.read('account.move', [invoice_id],
                               ['name', 'state', 'partner_id', 'amount_total', 'amount_residual', 'payment_state',
                                'reversal_move_ids'])
        return found.get(invoice_id)

    def grade_misfiled_payment(self, rec):
        invoice = self.invoice(rec['invoice_id'])
        payment = self.payment(rec['payment_id'])
        if invoice['payment_state'] not in ('paid', 'in_payment'):
            return False, f"{rec['invoice']} is still {invoice['payment_state'].replace('_', ' ')}."
        if payment and payment['state'] not in ('draft', 'canceled') and m2o_id(payment['partner_id']) == rec['wrong_partner_id']:
            return False, f"The payment is still recorded under {payment['partner_id'][1]}."
        if payment and rec['invoice_id'] not in payment['reconciled_invoice_ids'] and payment['state'] not in ('draft', 'canceled'):
            return False, "The misfiled payment was not the one applied to Harbor's invoice."
        return True, f"Payment moved to {invoice['partner_id'][1]} and applied to {rec['invoice']}; {rec['trap_invoice']} left open."

    def grade_duplicate_customer(self, rec):
        pair = [rec['original_partner_id'], rec['duplicate_partner_id']]
        partners = self.odoo.read('res.partner', list(self.odoo.exists('res.partner', pair)), ['name', 'active'])
        active = [p for p in partners.values() if p['active']]
        if len(active) != 1:
            return False, "Both contacts are still active; they were not merged."
        if len(partners) != 1:
            return False, "One contact was archived instead of merged; its records still point at the duplicate."
        survivor = active[0]
        invoice = self.invoice(rec['invoice_id'])
        payment = self.payment(rec['payment_id'])
        if m2o_id(invoice['partner_id']) != survivor['id'] or m2o_id(payment['partner_id']) != survivor['id']:
            return False, "Contacts merged, but the invoice and payment don't point at the same customer."
        if invoice['payment_state'] not in ('paid', 'in_payment'):
            return False, f"Contacts merged, but {rec['invoice']} is still {invoice['payment_state'].replace('_', ' ')}."
        return True, f"Merged into '{survivor['name']}' and {rec['invoice']} is paid."

    def grade_overbilled_invoice(self, rec):
        order = self.odoo.read('sale.order', [rec['order_id']], ['invoice_ids', 'partner_id'])[rec['order_id']]
        fields = ['name', 'move_type', 'state', 'amount_total', 'amount_residual']
        moves = self.odoo.read('account.move', order['invoice_ids'], fields)
        new_credits = self.odoo.search_read('account.move', [
            ('move_type', '=', 'out_refund'),
            ('partner_id', '=', m2o_id(order['partner_id'])),
            ('create_date', '>', self.since),
        ], fields)
        moves.update({m['id']: m for m in new_credits})
        posted = [m for m in moves.values() if m['state'] == 'posted']
        net = sum(m['amount_total'] * (1 if m['move_type'] == 'out_invoice' else -1) for m in posted)
        open_moves = [m['name'] for m in posted if not close(m['amount_residual'], 0.0)]
        if not close(net, rec['order_total']):
            return False, f"Net billed is ${net:,.2f}; the order is ${rec['order_total']:,.2f}."
        if open_moves:
            return False, f"Net billed matches the order, but {', '.join(open_moves)} still has a balance."
        credits = [m['name'] for m in posted if m['move_type'] == 'out_refund']
        return True, f"Credited ${rec['invoice_total'] - rec['order_total']:,.2f} ({', '.join(credits) or 'corrected'}); net billed equals the order and nothing is open."

    def grade_unidentified_wire(self, rec):
        payment = self.payment(rec['payment_id'])
        if not payment or payment['state'] in ('draft', 'canceled'):
            return False, "The wire was reset or cancelled instead of being left for follow-up."
        if not close(payment['amount'], rec['amount']):
            return False, f"The wire amount was changed to ${payment['amount']:,.2f}; the bank received ${rec['amount']:,.2f}."
        if payment['partner_id'] or payment['reconciled_invoice_ids']:
            who = payment['partner_id'][1] if payment['partner_id'] else 'an invoice'
            return False, f"The wire was force-matched to {who}."
        if not has_note_since(self.odoo, [('account.payment', payment['id']), ('account.move', m2o_id(payment['move_id']))], self.since):
            return False, "Left unapplied (correct), but no exception was logged on the payment."
        return True, "Left unapplied and flagged as an exception on the payment."

    def grade_disputed_short_payment(self, rec):
        invoice = self.invoice(rec['invoice_id'])
        payment = self.payment(rec['payment_id'])
        if invoice['reversal_move_ids']:
            return False, "A credit note was issued for a disputed amount."
        if rec['invoice_id'] not in payment['reconciled_invoice_ids']:
            return False, f"The payment was not applied to {rec['invoice']}."
        if not close(invoice['amount_residual'], rec['short_amount']):
            return False, f"{rec['invoice']} has ${invoice['amount_residual']:,.2f} open; expected the disputed ${rec['short_amount']:,.2f}."
        targets = [('account.move', rec['invoice_id']), ('account.payment', payment['id']), ('account.move', m2o_id(payment['move_id']))]
        if not has_note_since(self.odoo, targets, self.since):
            return False, "Payment applied with $450 left open (correct), but the dispute was not logged."
        return True, f"Payment applied, ${rec['short_amount']:,.0f} left open and the dispute logged."

    def grade_collateral(self):
        baseline = self.evidence['baseline']['invoices']
        current = self.odoo.read('account.move', [int(i) for i in baseline],
                                 ['name', 'partner_id', 'amount_residual', 'payment_state', 'state'])
        changed = [
            f"{before['name']} ({before['partner']})"
            for invoice_id, before in baseline.items()
            if (now := current.get(int(invoice_id))) is None
            or now['state'] != 'posted'
            or now['partner_id'][1] != before['partner']
            or not close(now['amount_residual'], before['amount_residual'])
        ]
        if changed:
            return False, f"Changed invoices that were fine: {', '.join(changed[:5])}{'...' if len(changed) > 5 else ''}"
        return True, f"All {len(baseline)} other invoices untouched."

    def run(self):
        rows = []
        for defect in self.evidence['defects']:
            passed, detail = getattr(self, f"grade_{defect['key']}")(defect['records'])
            rows.append({'id': defect['id'], 'title': defect['title'], 'story': defect['story'],
                         'expected': defect['expected'], 'passed': passed, 'detail': detail})
        passed, detail = self.grade_collateral()
        rows.append({'id': 'C', 'title': "Didn't break anything else", 'story': "Every invoice that wasn't planted must be exactly as it was.",
                     'expected': "No collateral changes.", 'passed': passed, 'detail': detail})
        return rows


STYLE = """
body { font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 40px 56px; }
h1 { font-size: 34px; margin: 0 0 4px; } .sub { color: #94a3b8; font-size: 17px; margin-bottom: 28px; }
.score { font-size: 64px; font-weight: 800; margin: 8px 0 28px; } .score small { font-size: 22px; color: #94a3b8; font-weight: 500; }
.row { display: grid; grid-template-columns: 70px 1fr 150px; gap: 20px; align-items: center; background: #1e293b; border-radius: 14px; padding: 18px 22px; margin-bottom: 12px; }
.id { font-size: 26px; font-weight: 800; color: #94a3b8; } .title { font-size: 21px; font-weight: 700; }
.story { color: #cbd5e1; font-size: 15px; margin-top: 4px; } .detail { font-size: 15px; margin-top: 6px; color: #f8fafc; }
.expected { font-size: 15px; margin-top: 6px; color: #fde68a; }
.pill { text-align: center; font-size: 22px; font-weight: 800; border-radius: 999px; padding: 10px 0; }
.pass { background: #166534; color: #dcfce7; } .fail { background: #991b1b; color: #fee2e2; } .key { background: #1d4ed8; color: #dbeafe; font-size: 16px; }
"""


def render_scorecard(evidence, rows):
    passed = sum(r['passed'] for r in rows)
    body = ''.join(
        f"<div class='row'><div class='id'>{r['id']}</div><div><div class='title'>{html.escape(r['title'])}</div>"
        f"<div class='story'>{html.escape(r['story'])}</div><div class='detail'>{html.escape(r['detail'])}</div></div>"
        f"<div class='pill {'pass' if r['passed'] else 'fail'}'>{'PASS' if r['passed'] else 'FAIL'}</div></div>"
        for r in rows
    )
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Month-end close: verifier</title><style>{STYLE}</style></head><body>"
            f"<h1>Month-end close verifier: {html.escape(evidence['company'])}</h1>"
            f"<div class='sub'>{html.escape(evidence['period'])} close. Graded against the answer key written when the data was seeded. "
            f"Graded {datetime.now():%Y-%m-%d %H:%M}.</div>"
            f"<div class='score'>{passed} / {len(rows)} <small>checks passed</small></div>{body}</body></html>")


def render_answer_key(evidence):
    body = ''.join(
        f"<div class='row'><div class='id'>{d['id']}</div><div><div class='title'>{html.escape(d['title'])}</div>"
        f"<div class='story'>{html.escape(d['story'])}</div>"
        + (f"<div class='story'>Trap: {html.escape(d['trap'])}</div>" if d.get('trap') else '')
        + f"<div class='expected'>Correct resolution: {html.escape(d['expected'])}</div></div>"
        f"<div class='pill key'>{'Fix in Odoo' if d['fixable_in_product'] else 'Exception'}</div></div>"
        for d in evidence['defects']
    )
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Month-end close: answer key</title><style>{STYLE}</style></head><body>"
            f"<h1>Answer key: {html.escape(evidence['company'])}</h1>"
            f"<div class='sub'>Planted when the data was seeded (seed {evidence['builder_seed']}). {len(evidence['defects'])} defects "
            f"hidden among {len(evidence['baseline']['invoices'])} ordinary invoices.</div>{body}</body></html>")


def dump_world(odoo):
    def rows(model, domain, fields):
        return [{k: (v[1] if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) else v) for k, v in r.items()}
                for r in odoo.search_read(model, domain, fields)]
    return {
        'partners': rows('res.partner', [('customer_rank', '>', 0)], ['name', 'email', 'phone', 'street', 'city', 'zip']),
        'products': rows('product.product', [('default_code', '=like', 'SOS-%')], ['default_code', 'name', 'list_price']),
        'orders': rows('sale.order', [], ['name', 'partner_id', 'date_order', 'amount_total', 'state']),
        'invoices': rows('account.move', [('move_type', 'in', ('out_invoice', 'out_refund'))],
                         ['name', 'partner_id', 'invoice_date', 'invoice_date_due', 'amount_total', 'amount_residual', 'payment_state']),
        'payments': rows('account.payment', [], ['name', 'partner_id', 'date', 'amount', 'memo', 'state', 'is_reconciled']),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', required=True)
    parser.add_argument('--url', default='http://localhost:8069')
    parser.add_argument('--login', default='admin')
    parser.add_argument('--password', default='admin')
    parser.add_argument('--evidence', help="Evidence JSON (default: ~/.local/share/month_end_close/<db>-evidence.json)")
    parser.add_argument('--html', help="Write the scorecard as HTML to this path")
    parser.add_argument('--answer-key', help="Write the answer key as HTML to this path and exit")
    parser.add_argument('--dump', help="Write a timestamp-free snapshot of the world to this path and exit")
    args = parser.parse_args()

    odoo = Odoo(args.url, args.db, args.login, args.password)
    if args.dump:
        with open(args.dump, 'w', encoding='utf-8') as handle:
            json.dump(dump_world(odoo), handle, indent=1, sort_keys=True, default=str)
        return 0

    directory = os.environ.get('MONTH_END_EVIDENCE_DIR') or os.path.expanduser('~/.local/share/month_end_close')
    with open(args.evidence or os.path.join(directory, f'{args.db}-evidence.json'), encoding='utf-8') as handle:
        evidence = json.load(handle)

    if args.answer_key:
        with open(args.answer_key, 'w', encoding='utf-8') as handle:
            handle.write(render_answer_key(evidence))
        print(f"Answer key written to {args.answer_key}")
        return 0

    rows = Grader(odoo, evidence).run()
    for row in rows:
        print(f"{'PASS' if row['passed'] else 'FAIL'}  {row['id']:>2}  {row['title']}: {row['detail']}")
    passed = sum(r['passed'] for r in rows)
    print(f"\n{passed}/{len(rows)} checks passed")
    if args.html:
        with open(args.html, 'w', encoding='utf-8') as handle:
            handle.write(render_scorecard(evidence, rows))
        print(f"Scorecard written to {args.html}")
    return 0 if passed == len(rows) else 1


if __name__ == '__main__':
    sys.exit(main())
