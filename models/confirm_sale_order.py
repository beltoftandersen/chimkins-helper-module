# /models/confirm_sale_order.py

from odoo import models, api, exceptions, _, fields
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model
    def confirm_order_by_id(self, sale_order_id, order_date=None):
        try:
            sale_order = self.browse(sale_order_id)
            if not sale_order.exists():
                msg = _("Sale Order with ID %s does not exist.") % sale_order_id
                raise exceptions.UserError(msg)

            forced_date = None
            if order_date:
                try:
                    forced_date = fields.Datetime.to_datetime(order_date)
                    _logger.info(
                        "Forcing confirmation date for sale.order %s to %s",
                        sale_order_id,
                        forced_date,
                    )
                except Exception as exc:
                    _logger.warning(
                        "Could not parse provided order_date %r for sale.order %s: %s",
                        order_date,
                        sale_order_id,
                        exc,
                    )

            if forced_date:
                sale_order.with_context(force_confirmation_date=forced_date).action_confirm()
            else:
                sale_order.action_confirm()

            log_message = _("Sale Order %s confirmed successfully.") % sale_order_id
            _logger.info(log_message)

            woocommerce_order_id = sale_order.woocommerce_order_id or ""

            return {
                "success": True,
                "message": "Sale Order confirmed successfully.",
                "log_message": log_message,
                "woocommerce_order_id": woocommerce_order_id,
            }

        except exceptions.UserError as ue:
            _logger.error("UserError confirming Sale Order %s: %s", sale_order_id, ue)
            return {
                "success": False,
                "message": str(ue),
                "log_message": str(ue),
                "woocommerce_order_id": "",
            }
        except Exception as e:
            _logger.exception("Unexpected error confirming Sale Order %s: %s", sale_order_id, e)
            return {
                "success": False,
                "message": str(e),
                "log_message": str(e),
                "woocommerce_order_id": "",
            }

    def action_confirm(self):
        res = super(SaleOrder, self).action_confirm()

        forced_date = self.env.context.get("force_confirmation_date")
        if forced_date:
            try:
                forced_dt = fields.Datetime.to_datetime(forced_date)
                self.write({"date_order": forced_dt})
                _logger.info(
                    "Applied forced confirmation date %s to sale.order ids %s",
                    forced_dt,
                    self.ids,
                )
            except Exception as exc:
                _logger.warning(
                    "Failed to apply forced confirmation date %r "
                    "to sale.order ids %s: %s",
                    forced_date,
                    self.ids,
                    exc,
                )

        return res