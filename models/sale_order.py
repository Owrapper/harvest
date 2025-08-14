from odoo import models, fields, api, _
from odoo.exceptions import UserError
import math


class SaleOrder(models.Model):
    _inherit = 'sale.order'
    
    # Add fields to track timesheet invoicing
    timesheet_hours_total = fields.Float(
        string='Total Timesheet Hours',
        compute='_compute_timesheet_hours',
        store=False
    )
    timesheet_days_total = fields.Float(
        string='Total Days (8hr/day)',
        compute='_compute_timesheet_hours',
        store=False
    )
    timesheet_ids_harvest = fields.One2many(
        'account.analytic.line',
        compute='_compute_timesheet_hours',
        string='Related Timesheets'
    )
    
    @api.depends('order_line.product_id')
    def _compute_timesheet_hours(self):
        for order in self:
            # Find all timesheets linked to this SO's lines
            timesheets = self.env['account.analytic.line'].search([
                ('so_line', 'in', order.order_line.ids),
            ])
            
            total_hours = sum(timesheets.mapped('unit_amount'))
            order.timesheet_hours_total = total_hours
            order.timesheet_days_total = total_hours / 8.0  # Convert to days
            order.timesheet_ids_harvest = timesheets
    
    def action_update_delivered_qty_from_timesheets(self):
        """Update delivered quantities based on timesheet hours (8 hours = 1 day)"""
        self.ensure_one()
        
        updated_lines = []
        for line in self.order_line.filtered(lambda l: l.product_id.type == 'service'):
            # Find timesheets for this specific line
            line_timesheets = self.env['account.analytic.line'].search([
                ('so_line', '=', line.id),
            ])
            
            if line_timesheets:
                line_hours = sum(line_timesheets.mapped('unit_amount'))
                line_days = line_hours / 8.0  # Convert to days
                
                # Update delivered quantity
                line.qty_delivered = line_days
                updated_lines.append(f"{line.product_id.name}: {line_hours:.1f} hours = {line_days:.2f} days")
        
        if updated_lines:
            message = _("Delivered quantities updated from timesheets:\n") + "\n".join(updated_lines)
            self.message_post(body=message)
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Delivered quantities updated from timesheets.'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            raise UserError(_('No timesheet entries found for service lines.'))
    
    def action_create_invoice_from_timesheets(self):
        """Create invoice based on timesheet hours converted to days (8 hours = 1 day)"""
        self.ensure_one()
        
        if self.state != 'sale':
            raise UserError(_('Please confirm the sales order first.'))
        
        # Check if there are timesheets
        if not self.timesheet_hours_total:
            raise UserError(_('No timesheet entries found for this sales order.'))
        
        # First update delivered quantities
        self.action_update_delivered_qty_from_timesheets()
        
        # Prepare invoice lines
        invoice_lines = []
        for line in self.order_line.filtered(lambda l: l.product_id.type == 'service' and l.qty_delivered > 0):
            # Create invoice line with delivered quantity (in days)
            invoice_line_vals = {
                'name': f"{line.name}\n{line.timesheet_hours:.1f} hours = {line.qty_delivered:.2f} days @ {line.price_unit:.2f}/day",
                'product_id': line.product_id.id,
                'quantity': line.qty_delivered,
                'price_unit': line.price_unit,
                'tax_ids': [(6, 0, line.tax_id.ids)],
                'sale_line_ids': [(4, line.id)],  # Link to SO line
            }
            invoice_lines.append((0, 0, invoice_line_vals))
        
        if not invoice_lines:
            raise UserError(_('No service lines with timesheets to invoice.'))
        
        # Create the invoice
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'invoice_origin': self.name,
            'ref': f'Timesheet Invoice for {self.name}',
            'invoice_line_ids': invoice_lines,
            'currency_id': self.currency_id.id,
            'invoice_payment_term_id': self.payment_term_id.id,
            'fiscal_position_id': self.fiscal_position_id.id,
        })
        
        # Return action to display the invoice
        return {
            'type': 'ir.actions.act_window',
            'name': _('Timesheet Invoice'),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
            'context': {'default_move_type': 'out_invoice'},
        }
    
    def action_view_timesheets(self):
        """View all timesheets related to this sales order"""
        self.ensure_one()
        
        return {
            'type': 'ir.actions.act_window',
            'name': _('Timesheets'),
            'res_model': 'account.analytic.line',
            'view_mode': 'tree,form',
            'domain': [('so_line', 'in', self.order_line.ids)],
            'context': {'default_so_line': self.order_line[0].id if self.order_line else False}
        }


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'
    
    timesheet_hours = fields.Float(
        string='Timesheet Hours',
        compute='_compute_timesheet_hours',
        store=False
    )
    timesheet_days = fields.Float(
        string='Timesheet Days',
        compute='_compute_timesheet_hours',
        store=False
    )
    
    @api.depends('product_id')
    def _compute_timesheet_hours(self):
        for line in self:
            timesheets = self.env['account.analytic.line'].search([
                ('so_line', '=', line.id),
            ])
            hours = sum(timesheets.mapped('unit_amount'))
            line.timesheet_hours = hours
            line.timesheet_days = hours / 8.0  # Convert to days