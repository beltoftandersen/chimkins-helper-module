# /models/sale_order.py

from odoo import models, api, _
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.model
    def cancel_woocommerce_sales_order(self, wc_order_id):
        try:
            sale_order = self.search([('woocommerce_order_id', '=', wc_order_id)], limit=1)
            if not sale_order:
                msg = _("No Sales Order found for WooCommerce Order ID %s.") % wc_order_id
                _logger.error(msg)
                return {
                    'success': False,
                    'message': msg,
                    'log_message': msg,
                    'woocommerce_order_id': wc_order_id,
                    'sale_order_id': None
                }

            _logger.info(f"Sales Order {sale_order.name} found with state: {sale_order.state}")

            if sale_order.state not in ['draft', 'sent', 'sale']:
                msg = _("Sales Order %s is not in a cancellable state.") % sale_order.name
                _logger.warning(msg)
                return {
                    'success': False,
                    'message': msg,
                    'log_message': msg,
                    'woocommerce_order_id': sale_order.woocommerce_order_id or '',
                    'sale_order_id': sale_order.id
                }

            pickings = sale_order.picking_ids.filtered(lambda p: p.state not in ['cancel', 'done'])
            for picking in pickings:
                try:
                    picking.action_cancel()
                    _logger.info(f"Picking {picking.name} canceled for Sales Order {sale_order.name}.")
                except Exception as e:
                    err_msg = _("Error canceling picking %s: %s") % (picking.name, str(e))
                    _logger.error(err_msg)
                    return {
                        'success': False,
                        'message': err_msg,
                        'log_message': err_msg,
                        'woocommerce_order_id': sale_order.woocommerce_order_id or '',
                        'sale_order_id': sale_order.id
                    }

            try:
                sale_order.action_cancel()
                if sale_order.state != 'cancel':
                    _logger.warning(f"Sales Order {sale_order.name} not fully canceled. Forcing state to cancel.")
                    sale_order.write({'state': 'cancel'})
                log_message = _("Sales Order %s has been cancelled.") % sale_order.name
                _logger.info(log_message)

                return {
                    'success': True,
                    'message': "Sales Order cancelled successfully.",
                    'log_message': log_message,
                    'woocommerce_order_id': sale_order.woocommerce_order_id or '',
                    'sale_order_id': sale_order.id
                }
            except Exception as e:
                err_msg = _("Error canceling Sales Order %s: %s") % (sale_order.name, str(e))
                _logger.error(err_msg)
                return {
                    'success': False,
                    'message': err_msg,
                    'log_message': err_msg,
                    'woocommerce_order_id': sale_order.woocommerce_order_id or '',
                    'sale_order_id': sale_order.id
                }

        except Exception as e:
            _logger.exception(f"Unexpected error canceling Sales Order for WC Order ID {wc_order_id}: {e}")
            return {
                'success': False,
                'message': str(e),
                'log_message': str(e),
                'woocommerce_order_id': wc_order_id,
                'sale_order_id': None
            }
