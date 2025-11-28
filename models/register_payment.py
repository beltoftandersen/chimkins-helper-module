# /models/register_payment.py

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class PaymentRegister(models.Model):
    _name = 'payment.register'
    _description = 'Payment Register'

    invoice_id = fields.Many2one('account.move', string='Invoice', required=True)
    journal_id = fields.Many2one('account.journal', string='Payment Journal', required=True)
    custom_payment_ref = fields.Char(string="Payment ref.")

    @api.model
    def register_payment(self, invoice_id, journal_id, payment_ref=None):
        try:
            invoice = self.env['account.move'].browse(invoice_id)
            if not invoice.exists():
                raise UserError(f"Invoice with ID {invoice_id} does not exist.")
            if invoice.state != 'posted':
                raise UserError(f"Invoice {invoice_id} is not in a posted state.")

            journal = self.env['account.journal'].browse(journal_id)
            if not journal.exists():
                raise UserError(f"Journal with ID {journal_id} does not exist.")

            context = {
                'active_model': 'account.move',
                'active_ids': [invoice.id],
            }

            payment_register_vals = {
                'journal_id': journal.id,
            }
            if hasattr(self.env['account.payment.register'], 'custom_payment_ref') and payment_ref:
                payment_register_vals['custom_payment_ref'] = payment_ref

            payment_register = self.env['account.payment.register'].with_context(**context).create(payment_register_vals)
            if not payment_register:
                raise UserError(f"Failed to create payment register for invoice {invoice_id}.")

            payments = payment_register.action_create_payments()
            if not payments:
                raise UserError(f"No payments were created for invoice {invoice_id}.")

            payment = payments[0] if len(payments) == 1 else None
            if payment and payment_ref and hasattr(payment, 'custom_payment_ref'):
                payment.custom_payment_ref = payment_ref
                _logger.info(f"Payment Reference '{payment_ref}' set for Payment ID {payment.id}")


            _logger.info(f"Payment registered successfully for {invoice.move_type} {invoice.name} using Journal '{journal.name}'.")

            invoice_ref = invoice.name or f"Move ID {invoice_id}"

            woocommerce_order_id = invoice.woocommerce_order_id or False

            if not woocommerce_order_id:
                sale_orders = invoice.line_ids.mapped('sale_line_ids.order_id')
                if sale_orders:
                    woocommerce_order_id = sale_orders[:1].woocommerce_order_id or False

            doc_type = "Credit Note" if invoice.move_type == 'out_refund' else "Invoice"
            log_message = f"Payment registered successfully for {doc_type} {invoice_ref} using Journal '{journal.name}'."

            return {
                "success": True,
                "message": f"Payment registered for {doc_type} {invoice_ref} using journal {journal.name}",
                "payment_register_id": payment_register.id,
                "invoice_ref": invoice_ref,
                "woocommerce_order_id": woocommerce_order_id,
                "move_type": invoice.move_type,
                "log_message": log_message
            }

        except UserError as ue:
            _logger.error(f"UserError during payment registration: {ue}")
            invoice_ref = 'Unknown'
            invoice = locals().get('invoice')
            if invoice and invoice.exists():
                invoice_ref = invoice.name
            return {
                "success": False,
                "message": str(ue),
                "payment_register_id": 0,
                "invoice_ref": invoice_ref,
                "woocommerce_order_id": False,
                "log_message": str(ue)
            }
        except Exception as e:
            _logger.exception(f"Unexpected error during payment registration: {e}")
            invoice_ref = 'Unknown'
            invoice = locals().get('invoice')
            if invoice and invoice.exists():
                invoice_ref = invoice.name
            return {
                "success": False,
                "message": str(e),
                "payment_register_id": 0,
                "invoice_ref": invoice_ref,
                "woocommerce_order_id": False,
                "log_message": str(e)
            }


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def assign_deliveries_for_paid_so(self):
        try:
            for order in self:
                _logger.info(f"[reset_first_delivery_to_assigned] Processing Sale Order: {order.name} (ID: {order.id})")

                pickings = self.env["stock.picking"].search([
                    ("origin", "=", order.name),
                    ("state", "=", "waiting")
                ], order="id asc", limit=1)

                if not pickings:
                    _logger.info(f"No 'Waiting' pickings found for {order.name}.")
                    continue

                picking = pickings[0]

                _logger.info(f"Checking stock availability for '{picking.name}'.")

                all_products_available = all(
                    move.product_id.qty_available >= move.product_uom_qty
                    for move in picking.move_ids_without_package
                )

                if all_products_available:
                    picking.write({"state": "assigned"})
                    _logger.info(f"Stock is available! Picking '{picking.name}' is now Ready (assigned).")
                    picking.message_post(
                        body=_("Delivery was set to 'Ready' because stock is available."),
                        subtype_xmlid="mail.mt_note"
                    )
                else:
                    _logger.warning(f"Not enough stock for '{picking.name}'. Keeping it in 'Waiting'.")
                    picking.message_post(
                        body=_("Delivery remains in 'Waiting' due to insufficient stock."),
                        subtype_xmlid="mail.mt_note"
                    )

            _logger.info("[reset_first_delivery_to_assigned] Finished processing all 'Waiting' pickings.")
            return True

        except Exception as e:
            _logger.exception("Error checking stock before setting the first delivery to 'assigned': %s", e)
            raise UserError(
                _("Failed to check stock before setting delivery to 'assigned'.\nError: %s") % e
            )


