# /models/sale_order.py

from odoo import models, api, exceptions, _
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model
    def confirm_order_by_id(self, sale_order_id):
        try:
            sale_order = self.browse(sale_order_id)
            if not sale_order.exists():
                msg = _("Sale Order with ID %s does not exist.") % sale_order_id
                raise exceptions.UserError(msg)

            sale_order.action_confirm()
            log_message = _("Sale Order %s confirmed successfully.") % sale_order_id
            _logger.info(log_message)

            woocommerce_order_id = sale_order.woocommerce_order_id or ""
            return {
                'success': True,
                'message': "Sale Order confirmed successfully.",
                'log_message': log_message,
                'woocommerce_order_id': woocommerce_order_id
            }

        except exceptions.UserError as ue:
            _logger.error(f"UserError confirming Sale Order {sale_order_id}: {ue}")
            return {
                'success': False,
                'message': str(ue),
                'log_message': str(ue),
                'woocommerce_order_id': ""
            }
        except Exception as e:
            _logger.exception(f"Unexpected error confirming Sale Order {sale_order_id}: {e}")
            return {
                'success': False,
                'message': str(e),
                'log_message': str(e),
                'woocommerce_order_id': ""
            }
