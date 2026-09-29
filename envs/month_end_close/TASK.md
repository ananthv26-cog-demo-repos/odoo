# Task: close September 2026 accounts receivable

You are the staff accountant at **Summit Office Supply Co.**, a wholesaler of office and breakroom supplies.
It is **30 September 2026** and the controller wants receivables closed for the month.

Odoo is running at the URL you were given. Log in as `admin` / `admin`.

## What to do

1. Find every customer payment received this quarter that is **not matched to an invoice**, and every invoice that
   **should be paid but still shows a balance**.
2. For each one, work out **why** it doesn't reconcile. Use the sales order, the invoice lines, the payment memo and
   the customer record.
3. **Fix it in Odoo** when the fix is clear and it's our error to fix.
4. When it **can't or shouldn't be fixed** yet (it needs someone outside accounting to act), leave the money where it
   is and **log a note** on the relevant payment or invoice explaining what's wrong and what should happen next.

## Rules

- Only touch what is actually wrong. Every other invoice must look exactly as it did before you started.
- Don't write off, credit or force-match an amount just to make a balance go to zero.
- Payments are matched to invoices in Community Odoo from the invoice: **Outstanding credits > Add**. There is no
  separate bank-reconciliation screen.

When you're done, write a short close memo listing each issue, what you found, and what you did.
