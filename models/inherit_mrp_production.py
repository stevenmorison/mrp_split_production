from odoo import _, models, fields
from odoo.exceptions import UserError


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    production_capacity = fields.Float(compute='_compute_production_capacity', help="Quantity that can be produced with the current stock of components")
    
    def action_open_workorder_split(self):
        # Lakukan logika apa pun yang diperlukan sebelum membuka form pemisahan work order
        # Misalnya, Anda dapat memvalidasi apakah produksi memiliki work order yang bisa dipecah
        # Jika validasi berhasil, buka form pemisahan work order
        return {
            'name': 'Split Work Order',
            'type': 'ir.actions.act_window',
            'res_model': 'mrp.workorder.split',
            'view_mode': 'form',
            'target': 'new',
        }

    """action to open to Split Production form"""
    
    def action_split(self):
        self._pre_action_split_merge_hook(split=True)
        if len(self) > 1:
            productions = [Command.create({'production_id': production.id}) for production in self]
            # Wizard need a real id to have buttons enable in the view
            wizard = self.env['mrp_split_production.production.split.multi'].create({'production_ids': productions})
            action = self.env['ir.actions.actions']._for_xml_id('mrp_split_production.action_mrp_production_split_multi')
            action['res_id'] = wizard.id
            return action
        else:
            action = self.env['ir.actions.actions']._for_xml_id('mrp_split_production.action_mrp_production_split')
            action['context'] = {
                'default_production_id': self.id,
            }
            return action

    def action_merge(self):
            self._pre_action_split_merge_hook(merge=True)
            products = set([(production.product_id, production.bom_id) for production in self])
            product_id, bom_id = products.pop()
            users = set([production.user_id for production in self])
            if len(users) == 1:
                user_id = users.pop()
            else:
                user_id = self.env.user

            origs = self._prepare_merge_orig_links()
            dests = {}
            for move in self.move_finished_ids:
                dests.setdefault(move.byproduct_id.id, []).extend(move.move_dest_ids.ids)

            production = self.env['mrp.production'].with_context(default_picking_type_id=self.picking_type_id.id).create({
                'product_id': product_id.id,
                'bom_id': bom_id.id,
                'picking_type_id': self.picking_type_id.id,
                'product_qty': sum(production.product_uom_qty for production in self),
                'product_uom_id': product_id.uom_id.id,
                'user_id': user_id.id,
                'origin': ",".join(sorted([production.name for production in self])),
            })

            for move in production.move_raw_ids:
                for field, vals in origs[move.bom_line_id.id].items():
                    move[field] = vals

            for move in production.move_finished_ids:
                move.move_dest_ids = [Command.set(dests[move.byproduct_id.id])]

            self.move_dest_ids.created_production_id = production.id

            self.procurement_group_id.stock_move_ids.group_id = production.procurement_group_id

            if 'confirmed' in self.mapped('state'):
                production.move_raw_ids._adjust_procure_method()
                (production.move_raw_ids | production.move_finished_ids).write({'state': 'confirmed'})
                production.action_confirm()

            self.with_context(skip_activity=True)._action_cancel()
            # set the new deadline of origin moves (stock to pre prod)
            production.move_raw_ids.move_orig_ids.with_context(date_deadline_propagate_ids=set(production.move_raw_ids.ids)).write({'date_deadline': production.date_start})
            for p in self:
                p._message_log(body=_('This production has been merge in %s', production.display_name))

            return {
                'type': 'ir.actions.act_window',
                'res_model': 'mrp.production',
                'view_mode': 'form',
                'res_id': production.id,
            }        

    def _pre_action_split_merge_hook(self, merge=False, split=False):
            if not merge and not split:
                return True
            ope_str = merge and _('merged') or _('split')
            if any(production.state not in ('draft', 'confirmed') for production in self):
                raise UserError(_("Only manufacturing orders in either a draft or confirmed state can be %s.", ope_str))
            if any(not production.bom_id for production in self):
                raise UserError(_("Only manufacturing orders with a Bill of Materials can be %s.", ope_str))
            if split:
                return True

            if len(self) < 2:
                raise UserError(_("You need at least two production orders to merge them."))
            products = set([(production.product_id, production.bom_id) for production in self])
            if len(products) > 1:
                raise UserError(_('You can only merge manufacturing orders of identical products with same BoM.'))
            additional_raw_ids = self.mapped("move_raw_ids").filtered(lambda move: not move.bom_line_id)
            additional_byproduct_ids = self.mapped('move_byproduct_ids').filtered(lambda move: not move.byproduct_id)
            if additional_raw_ids or additional_byproduct_ids:
                raise UserError(_("You can only merge manufacturing orders with no additional components or by-products."))
            if len(set(self.mapped('state'))) > 1:
                raise UserError(_("You can only merge manufacturing with the same state."))
            if len(set(self.mapped('picking_type_id'))) > 1:
                raise UserError(_('You can only merge manufacturing with the same operation type'))
            # TODO explode and check no quantity has been edited
            return True    