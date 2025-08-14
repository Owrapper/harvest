from odoo import models, fields, api, _
from odoo.exceptions import UserError


class HarvestTimesheetWizard(models.TransientModel):
    _name = 'harvest.timesheet.wizard'
    _description = 'Create Timesheets from Harvest'

    harvest_entry_ids = fields.Many2many('harvest.time.entry', string='Harvest Entries')
    entry_count = fields.Integer('Number of Entries', compute='_compute_entry_count')
    
    # Global settings
    default_task_id = fields.Many2one('project.task', string='Default Task', 
                                     help='Task to assign to all timesheets (leave empty to use project defaults)')
    default_so_line_id = fields.Many2one('sale.order.line', string='Default Sales Order Line',
                                        help='Sales order line to assign to all timesheets')
    
    # Assignment mode
    assignment_mode = fields.Selection([
        ('auto', 'Auto-assign based on project'),
        ('manual', 'Use settings below for all entries')
    ], string='Assignment Mode', default='auto', required=True)

    @api.depends('harvest_entry_ids')
    def _compute_entry_count(self):
        for wizard in self:
            wizard.entry_count = len(wizard.harvest_entry_ids)

    def action_create_timesheets(self):
        """Create timesheets with global settings"""
        created_count = 0
        
        for harvest_entry in self.harvest_entry_ids:
            if not harvest_entry.timesheet_id and harvest_entry.harvest_user_id.employee_id:
                project = harvest_entry.harvest_project_id.project_id
                if not project:
                    continue
                
                # Determine task and SO line based on mode
                if self.assignment_mode == 'manual':
                    task_id = self.default_task_id.id if self.default_task_id else False
                    so_line_id = self.default_so_line_id.id if self.default_so_line_id else False
                else:
                    # Auto mode - use smart defaults
                    task_id, so_line_id = self._get_auto_assignments(harvest_entry, project)
                
                timesheet_vals = {
                    'name': harvest_entry.notes or '/',
                    'project_id': project.id,
                    'task_id': task_id,
                    'so_line': so_line_id,
                    'employee_id': harvest_entry.harvest_user_id.employee_id.id,
                    'date': harvest_entry.spent_date,
                    'unit_amount': harvest_entry.hours,
                }
                timesheet = self.env['account.analytic.line'].create(timesheet_vals)
                harvest_entry.timesheet_id = timesheet.id
                created_count += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('%d timesheet entries created.') % created_count,
                'type': 'success',
                'sticky': False,
            }
        }
    
    def _get_auto_assignments(self, harvest_entry, project):
        """Get automatic task and SO line assignments"""
        # Find active task
        task = self.env['project.task'].search([
            ('project_id', '=', project.id),
            ('stage_id.fold', '=', False)
        ], limit=1)
        
        # Find SO line if available
        so_line = False
        if hasattr(self.env['sale.order.line'], 'project_id'):
            so_line = self.env['sale.order.line'].search([
                ('project_id', '=', project.id),
                ('state', 'in', ['sale', 'done'])
            ], order='id desc', limit=1)
        
        return task.id if task else False, so_line.id if so_line else False


