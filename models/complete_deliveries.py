# custom_addons/helper_module/models/complete_deliveries.py

from odoo import models, _
from odoo.tools.float_utils import float_compare
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

                # Ready is not the same as every line reserved. A picking can
                # sit in "assigned" while individual moves have nothing behind
                # them, and assign_deliveries_for_paid_so used to force that
                # state without reserving at all. Validating then shipped what
                # was reserved and cancelled the rest, so a customer who had
                # been invoiced for ten units received six and Odoo recorded
                # the delivery as complete. Judge every move, not the header.
                short = [
                    move for move in picking.move_ids
                    if move.state not in ("done", "cancel")
                    and float_compare(
                        move.reserved_availability, move.product_uom_qty,
                        precision_rounding=move.product_uom.rounding,
                    ) < 0
                ]
                if short:
                    _logger.warning(
                        "Leaving %s open for order %s: %s of %s lines are not fully "
                        "reserved (%s).",
                        picking.name, order.name, len(short), len(picking.move_ids),
                        ", ".join(m.product_id.display_name for m in short),
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

                    # No skip_backorder here. It does not skip a dialog, it
                    # cancels whatever was not picked - which is what silently
                    # dropped those lines. Nothing should be short by this
                    # point, and if anything is, Odoo leaving a backorder is
                    # the outcome we want.
                    picking.with_context(
                        skip_delivery_email=True,
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
