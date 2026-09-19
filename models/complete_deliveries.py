# custom_addons/helper_module/models/complete_deliveries.py

from odoo import models, _
import logging

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def complete_deliveries_for_paid_so(self):
        """Validate every delivery for this order that can be fully reserved.

        Called by the middleware after payment is registered. Pickings are
        processed in id order because a two-step warehouse creates WH/PICK
        before WH/OUT, and WH/OUT only becomes reservable once the pick is
        done - so each one is re-assigned immediately before it is judged.

        A picking that cannot be fully reserved is left open, never forced.
        This database is shared with two live stores and must not go negative.
        """
        validated, skipped = [], []
        woocommerce_order_id = False

        for order in self:
            woocommerce_order_id = order.woocommerce_order_id or False

            pickings = order.picking_ids.filtered(
                lambda p: p.state not in ("done", "cancel")
            ).sorted("id")

            for picking in pickings:
                # Re-assign now: the previous picking in the chain may have
                # just made this one's stock available.
                picking.action_assign()

                if picking.state != "assigned":
                    _logger.info(
                        "Leaving %s open for order %s: state is %s, not fully reserved.",
                        picking.name, order.name, picking.state,
                    )
                    skipped.append(picking.name)
                    continue

                try:
                    # Without a quantity done, button_validate returns the
                    # Immediate Transfer wizard instead of completing, which
                    # cannot be driven over XML-RPC. Take exactly what is
                    # reserved, never more.
                    #
                    # Plain attribute access on purpose. A getattr fallback
                    # here would turn a renamed field into qty_done = 0, and
                    # validation would then quietly produce an empty transfer
                    # on a sale whose goods have physically left. Raising is
                    # the better failure. reserved_uom_qty is the one to use:
                    # it is in the line's own UoM, the same UoM qty_done is
                    # in, whereas reserved_qty is in the product's reference
                    # UoM and would be wrong for any UoM-converted line.
                    for line in picking.move_line_ids:
                        line.qty_done = line.reserved_uom_qty

                    picking.with_context(
                        skip_delivery_email=True,
                        skip_backorder=True,
                        picking_ids_not_to_backorder=picking.ids,
                    ).button_validate()
                except Exception as exc:
                    _logger.warning(
                        "Could not validate %s for order %s: %s",
                        picking.name, order.name, exc,
                    )
                    skipped.append(picking.name)
                    continue

                # button_validate can return a wizard action rather than
                # completing. Trust the state, not the return value.
                picking.invalidate_recordset(["state"])
                if picking.state == "done":
                    validated.append(picking.name)
                else:
                    _logger.warning(
                        "%s did not reach done for order %s (state %s).",
                        picking.name, order.name, picking.state,
                    )
                    skipped.append(picking.name)

        log_message = _("Validated: %s. Left open: %s.") % (
            ", ".join(validated) or _("none"),
            ", ".join(skipped) or _("none"),
        )
        _logger.info("complete_deliveries_for_paid_so: %s", log_message)

        return {
            "success": True,
            "log_message": log_message,
            "woocommerce_order_id": woocommerce_order_id,
            "validated": validated,
            "skipped": skipped,
        }
