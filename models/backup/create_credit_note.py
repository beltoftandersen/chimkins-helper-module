# /models/create_credit_note.py

from odoo import models, api, fields, _
from odoo.exceptions import UserError
import logging

logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def set_to_invoice_status(self):
        try:
            self.write({'invoice_status': 'to invoice'})
            self.message_post(
                body=_("Invoice status has been set to 'To Invoice'."),
                subtype_xmlid='mail.mt_note'
            )
            return True 
        except Exception as e:
            self.env.cr.rollback()
            raise ValueError(_("Failed to update the invoice status: %s") % str(e))

    def reset_all_deliveries_to_waiting(self):
        try:
            for order in self:
                pickings = self.env['stock.picking'].search([
                    ('origin', '=', order.name),
                    ('state', 'in', ['confirmed', 'assigned'])  # Include both states
                ])
                
                if not pickings:
                    logger.info(f"No 'Confirmed' or 'Assigned' deliveries found for Sales Order {order.name}.")
                    continue

                for picking in pickings:
                    logger.info(f"Resetting delivery {picking.name} to 'Waiting' (Confirmed).")
                    picking.write({'state': 'waiting'})  # Set to "Waiting"

                logger.info(f"All eligible deliveries for {order.name} have been reset to 'Waiting'.")

            return True

        except Exception as e:
            logger.exception(f"Error resetting deliveries for Sales Order {order.name}: {e}")
            raise UserError(
                _("Failed to reset deliveries to 'Waiting'.\nError: %s") % e
            )


        except Exception as e:
            logger.exception(f"Error resetting deliveries for Sales Order {self.name}: {e}")
            raise UserError(_("Failed to reset deliveries to waiting. Please check the logs for details."))


    def action_create_credit_note(self, refund_data):
        self.ensure_one()
        try:
            invoice = self.invoice_ids.filtered(lambda inv: inv.state == 'posted')
            if not invoice:
                msg = _("No posted invoice found for Sales Order %s") % self.name
                logger.error(msg)
                return {'success': False, 'message': msg, 'log_message': msg, 'credit_note_ids': [], 'woocommerce_order_id': self.woocommerce_order_id or ''}

            if len(invoice) > 1:
                msg = _("Multiple posted invoices found for Sales Order %s. Please process one at a time.") % self.name
                logger.error(msg)
                return {'success': False, 'message': msg, 'log_message': msg, 'credit_note_ids': [], 'woocommerce_order_id': self.woocommerce_order_id or ''}

            invoice = invoice[0]
            created_credit_notes = []

            for refund in refund_data.get('refunds', []):
                refund_id = refund.get('id', '')
                logger.info(f"Processing refund: {refund_id} for invoice {invoice.name}")

                refund_line_items = refund.get('line_items', [])
                refund_shipping_lines = refund.get('shipping_lines', [])

                lines_to_refund = []
                for line_item in refund_line_items:
                    woo_product_id = line_item.get('variation_id') or line_item.get('product_id')
                    woo_product_sku = line_item.get('sku')
                    quantity_to_refund = line_item.get('quantity', 0)

                    if not woo_product_id or quantity_to_refund == 0:
                        logger.warning(f"Skipping line item with woo_product_id={woo_product_id} and quantity={quantity_to_refund}")
                        continue

                    product = self.env['product.product'].search([('default_code', '=', woo_product_sku)], limit=1)

                    if not product:
                        msg = _("Product with WooCommerce product_id %s not found in Odoo.") % woo_product_id
                        logger.error(msg)
                        return {'success': False, 'message': msg, 'log_message': msg, 'credit_note_ids': [], 'woocommerce_order_id': self.woocommerce_order_id or ''}

                    invoice_line = invoice.invoice_line_ids.filtered(lambda l: l.product_id == product)
                    if not invoice_line:
                        msg = _("Product with WooCommerce product_id %s not found in invoice %s") % (woo_product_id, invoice.name)
                        logger.error(msg)
                        return {'success': False, 'message': msg, 'log_message': msg, 'credit_note_ids': [], 'woocommerce_order_id': self.woocommerce_order_id or ''}

                    invoice_line = invoice_line[0]
                    lines_to_refund.append((0, 0, {
                        'product_id': product.id,
                        'quantity': abs(quantity_to_refund),
                        'price_unit': invoice_line.price_unit,
                        'name': invoice_line.name,
                        'tax_ids': [(6, 0, invoice_line.tax_ids.ids)],
                    }))

                shipping_lines = refund.get("shipping_lines", [])
                for shipping in shipping_lines:
                    shipping_total_excl_tax = abs(float(shipping.get("total", 0.0)))
                    shipping_total_tax = abs(float(shipping.get("total_tax", 0.0)))

                    shipping_total = shipping_total_excl_tax + shipping_total_tax

                    shipping_products = self.env["product.product"].search([("default_code", "=", "SHIPPING_COST")], limit=1)
                    if not shipping_products:
                        raise UserError(_("Shipping cost product not found in Odoo. Please create a product with SKU 'SHIPPING_COST'."))

                    shipping_product_id = shipping_products.id

                    lines_to_refund.append((0, 0, {
                        "product_id": shipping_product_id,
                        "quantity": 1,
                        "price_unit": shipping_total,
                        "name": shipping.get("method_title", _("Shipping")),
                        "tax_ids": [(6, 0, invoice.line_ids.mapped("tax_ids").ids)],
                    }))

                if not refund_line_items and not refund_shipping_lines:
                    msg = _("No line items or shipping lines specified for refund ID %s") % refund_id
                    logger.error(msg)
                    return {'success': False, 'message': msg, 'log_message': msg, 'credit_note_ids': [], 'woocommerce_order_id': self.woocommerce_order_id or ''}

                reason = refund.get('reason', '')
                credit_note_vals = {
                    'move_type': 'out_refund',
                    'partner_id': invoice.partner_id.id,
                    'journal_id': invoice.journal_id.id,
                    'invoice_line_ids': lines_to_refund,
                    'ref': reason,
                    'invoice_origin': self.name,
                    'reversed_entry_id': invoice.id,
                    'woocommerce_order_id': self.woocommerce_order_id,
                    'woocommerce_refund_id': str(refund_id),
                }

                logger.info(f"Credit note values: {credit_note_vals}")
                credit_note = self.env['account.move'].create(credit_note_vals)
                credit_note.action_post()
                created_credit_notes.append(credit_note.id)
                logger.info(f"Credit note created: {credit_note.name} (ID: {credit_note.id})")

                logger.info(f"Reconciling invoice {invoice.name} with credit note {credit_note.name}")

                invoice.message_post(
                    body=_("A credit note %s has been created for refund %s.") % (credit_note.name, refund_id),
                    subtype_xmlid='mail.mt_note'
                )
                self.message_post(
                    body=_("A credit note %s has been created for Invoice %s, related to refund %s.") % (credit_note.name, invoice.name, refund_id),
                    subtype_xmlid='mail.mt_note'
                )

            if not created_credit_notes:
                msg = _("No new credit notes created. Possibly due to duplicates or no valid line items.")
                logger.info(msg)
                return {'success': True, 'message': msg, 'log_message': msg, 'credit_note_ids': [], 'woocommerce_order_id': self.woocommerce_order_id or ''}

            msg = _("Credit note(s) created successfully.")
            logger.info(msg)
            return {
                'success': True,
                'message': "Credit note(s) created successfully.",
                'log_message': msg,
                'credit_note_ids': created_credit_notes,
                'woocommerce_order_id': self.woocommerce_order_id or ''
            }

        except UserError as ue:
            logger.error(f"UserError creating credit note: {ue}")
            return {'success': False, 'message': str(ue), 'log_message': str(ue), 'credit_note_ids': [], 'woocommerce_order_id': self.woocommerce_order_id or ''}
        except Exception as e:
            logger.exception(f"Unexpected error creating credit note: {e}")
            return {'success': False, 'message': str(e), 'log_message': str(e), 'credit_note_ids': [], 'woocommerce_order_id': self.woocommerce_order_id or ''}

