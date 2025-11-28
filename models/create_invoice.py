# /models/create_invoice.py

from odoo import models, api, exceptions, _
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _prepare_invoice(self):
        """ Copy woocommerce_order_id from the sale order to the invoice. """
        invoice_vals = super(SaleOrder, self)._prepare_invoice()
        invoice_vals['woocommerce_order_id'] = self.woocommerce_order_id
        return invoice_vals

    def action_create_and_post_invoice(self):
        self.ensure_one()
        if self.state not in ['sale', 'done']:
            raise exceptions.UserError(_("Invoices can only be created for Sales Orders in 'sale' or 'done' state."))
        invoices = self._create_invoices()
        if not invoices:
            raise exceptions.UserError(_("No invoices were created."))
        invoices.action_post()
        return invoices.ids

    @api.model
    def create_invoice_by_order_id(self, sale_order_id):
        order = self.browse(sale_order_id)
        if not order.exists():
            raise exceptions.UserError(_("No Sales Order found with ID %s." % sale_order_id))

        try:
            invoice_ids = order.action_create_and_post_invoice()
            log_message = _("Invoice(s) created and posted successfully for Sales Order %s." % order.name)
            _logger.info(log_message)

            return {
                'success': True,
                'message': _("Invoice(s) created and posted for Sales Order %s." % order.name),
                'invoice_ids': invoice_ids,
                'log_message': log_message,
                'woocommerce_order_id': order.woocommerce_order_id or '',
            }

        except exceptions.UserError as ue:
            log_message = _("Error creating invoice for Sales Order %s: %s" % (order.name, str(ue)))
            _logger.error(log_message)
            return {
                'success': False,
                'message': str(ue),
                'invoice_ids': [],
                'log_message': log_message,
                'woocommerce_order_id': order.woocommerce_order_id or '',
            }

        except Exception as e:
            log_message = _("Unexpected error for Sales Order %s: %s" % (order.name, str(e)))
            _logger.exception(log_message)
            return {
                'success': False,
                'message': _("An unexpected error occurred."),
                'invoice_ids': [],
                'log_message': log_message,
                'woocommerce_order_id': order.woocommerce_order_id or '',
            }
