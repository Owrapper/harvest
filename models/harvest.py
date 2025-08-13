from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import json
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class HarvestConfig(models.Model):
    _name = 'harvest.config'
    _description = 'Harvest API Configuration'
    _rec_name = 'account_id'
    
    account_id = fields.Char('Harvest Account ID', required=True)
    access_token = fields.Char('Personal Access Token', required=True)
    api_url = fields.Char('API Base URL', default='https://api.harvestapp.com/v2/', readonly=True)
    last_sync = fields.Datetime('Last Synchronization')
    sync_days_back = fields.Integer('Days to Sync Back', default=30, help="Number of days to sync retroactively")
    active = fields.Boolean('Active', default=True)
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)
    
    @api.constrains('active')
    def _check_single_active(self):
        if self.active:
            existing = self.search([('active', '=', True), ('company_id', '=', self.company_id.id), ('id', '!=', self.id)])
            if existing:
                raise UserError(_('Only one active Harvest configuration is allowed per company.'))
    
    def _get_headers(self):
        return {
            'Harvest-Account-ID': self.account_id,
            'Authorization': f'Bearer {self.access_token}',
            'User-Agent': 'Odoo Harvest Integration',
            'Content-Type': 'application/json'
        }
    
    def test_connection(self):
        try:
            response = requests.get(
                f'{self.api_url}company',
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Connection to Harvest API successful!'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(_('Connection failed: %s') % response.text)
        except Exception as e:
            raise UserError(_('Connection failed: %s') % str(e))
    
    def sync_harvest_data(self):
        self.ensure_one()
        self.sync_users()
        self.sync_projects()
        self.sync_time_entries()
        self.last_sync = fields.Datetime.now()
        return True
    
    def sync_users(self):
        try:
            response = requests.get(
                f'{self.api_url}users',
                headers=self._get_headers(),
                params={'is_active': 'true'},
                timeout=30
            )
            if response.status_code == 200:
                users_data = response.json()
                for user in users_data.get('users', []):
                    self._create_or_update_user(user)
        except Exception as e:
            _logger.error(f'Failed to sync users: {str(e)}')
    
    def _create_or_update_user(self, harvest_user):
        HarvestUser = self.env['harvest.user']
        existing = HarvestUser.search([('harvest_id', '=', harvest_user['id'])], limit=1)
        
        values = {
            'harvest_id': harvest_user['id'],
            'name': f"{harvest_user.get('first_name', '')} {harvest_user.get('last_name', '')}".strip(),
            'email': harvest_user.get('email'),
            'is_active': harvest_user.get('is_active', False),
            'config_id': self.id,
        }
        
        if existing:
            existing.write(values)
        else:
            employee = self.env['hr.employee'].search([('work_email', '=', harvest_user.get('email'))], limit=1)
            if employee:
                values['employee_id'] = employee.id
            HarvestUser.create(values)
    
    def sync_projects(self):
        try:
            response = requests.get(
                f'{self.api_url}projects',
                headers=self._get_headers(),
                params={'is_active': 'true'},
                timeout=30
            )
            if response.status_code == 200:
                projects_data = response.json()
                for project in projects_data.get('projects', []):
                    self._create_or_update_project(project)
        except Exception as e:
            _logger.error(f'Failed to sync projects: {str(e)}')
    
    def _create_or_update_project(self, harvest_project):
        HarvestProject = self.env['harvest.project']
        existing = HarvestProject.search([('harvest_id', '=', harvest_project['id'])], limit=1)
        
        values = {
            'harvest_id': harvest_project['id'],
            'name': harvest_project.get('name'),
            'code': harvest_project.get('code'),
            'is_active': harvest_project.get('is_active', False),
            'budget': harvest_project.get('budget'),
            'config_id': self.id,
        }
        
        if existing:
            existing.write(values)
        else:
            HarvestProject.create(values)
    
    def sync_time_entries(self):
        try:
            date_from = fields.Date.today() - timedelta(days=self.sync_days_back)
            date_to = fields.Date.today()
            
            page = 1
            while True:
                response = requests.get(
                    f'{self.api_url}time_entries',
                    headers=self._get_headers(),
                    params={
                        'from': date_from.strftime('%Y-%m-%d'),
                        'to': date_to.strftime('%Y-%m-%d'),
                        'page': page,
                        'per_page': 100
                    },
                    timeout=30
                )
                
                if response.status_code == 200:
                    data = response.json()
                    for entry in data.get('time_entries', []):
                        self._create_or_update_time_entry(entry)
                    
                    if page >= data.get('total_pages', 1):
                        break
                    page += 1
                else:
                    _logger.error(f'Failed to sync time entries: {response.text}')
                    break
                    
        except Exception as e:
            _logger.error(f'Failed to sync time entries: {str(e)}')
    
    def _create_or_update_time_entry(self, harvest_entry):
        HarvestTimeEntry = self.env['harvest.time.entry']
        existing = HarvestTimeEntry.search([('harvest_id', '=', harvest_entry['id'])], limit=1)
        
        harvest_user = self.env['harvest.user'].search([('harvest_id', '=', harvest_entry['user']['id'])], limit=1)
        harvest_project = self.env['harvest.project'].search([('harvest_id', '=', harvest_entry['project']['id'])], limit=1)
        
        values = {
            'harvest_id': harvest_entry['id'],
            'spent_date': harvest_entry.get('spent_date'),
            'hours': harvest_entry.get('hours', 0.0),
            'notes': harvest_entry.get('notes'),
            'is_locked': harvest_entry.get('is_locked', False),
            'is_running': harvest_entry.get('is_running', False),
            'harvest_user_id': harvest_user.id if harvest_user else False,
            'harvest_project_id': harvest_project.id if harvest_project else False,
            'config_id': self.id,
        }
        
        if existing:
            existing.write(values)
        else:
            HarvestTimeEntry.create(values)


class HarvestUser(models.Model):
    _name = 'harvest.user'
    _description = 'Harvest User'
    
    harvest_id = fields.Integer('Harvest ID', required=True, index=True)
    name = fields.Char('Name', required=True)
    email = fields.Char('Email')
    employee_id = fields.Many2one('hr.employee', string='Employee')
    is_active = fields.Boolean('Active')
    config_id = fields.Many2one('harvest.config', string='Configuration', required=True, ondelete='cascade')
    
    _sql_constraints = [
        ('harvest_id_uniq', 'unique(harvest_id, config_id)', 'Harvest ID must be unique per configuration!')
    ]


class HarvestProject(models.Model):
    _name = 'harvest.project'
    _description = 'Harvest Project'
    
    harvest_id = fields.Integer('Harvest ID', required=True, index=True)
    name = fields.Char('Name', required=True)
    code = fields.Char('Code')
    project_id = fields.Many2one('project.project', string='Odoo Project')
    is_active = fields.Boolean('Active')
    budget = fields.Float('Budget')
    config_id = fields.Many2one('harvest.config', string='Configuration', required=True, ondelete='cascade')
    time_entry_ids = fields.One2many('harvest.time.entry', 'harvest_project_id', string='Time Entries')
    
    _sql_constraints = [
        ('harvest_id_uniq', 'unique(harvest_id, config_id)', 'Harvest ID must be unique per configuration!')
    ]
    
    def action_view_time_entries(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Time Entries'),
            'res_model': 'harvest.time.entry',
            'view_mode': 'tree,form',
            'domain': [('harvest_project_id', '=', self.id)],
            'context': {'default_harvest_project_id': self.id}
        }


class HarvestTimeEntry(models.Model):
    _name = 'harvest.time.entry'
    _description = 'Harvest Time Entry'
    _order = 'spent_date desc'
    
    harvest_id = fields.Integer('Harvest ID', required=True, index=True)
    spent_date = fields.Date('Date', required=True)
    hours = fields.Float('Hours', required=True)
    notes = fields.Text('Notes')
    is_locked = fields.Boolean('Locked')
    is_running = fields.Boolean('Running')
    harvest_user_id = fields.Many2one('harvest.user', string='Harvest User', required=True)
    harvest_project_id = fields.Many2one('harvest.project', string='Harvest Project', required=True)
    timesheet_id = fields.Many2one('account.analytic.line', string='Timesheet Entry')
    config_id = fields.Many2one('harvest.config', string='Configuration', required=True, ondelete='cascade')
    
    _sql_constraints = [
        ('harvest_id_uniq', 'unique(harvest_id, config_id)', 'Harvest ID must be unique per configuration!')
    ]
    
    def create_timesheet_entries(self):
        created_count = 0
        for entry in self:
            if not entry.timesheet_id and entry.harvest_user_id.employee_id and entry.harvest_project_id.project_id:
                timesheet_vals = {
                    'name': entry.notes or '/',
                    'project_id': entry.harvest_project_id.project_id.id,
                    'employee_id': entry.harvest_user_id.employee_id.id,
                    'date': entry.spent_date,
                    'unit_amount': entry.hours,
                }
                timesheet = self.env['account.analytic.line'].create(timesheet_vals)
                entry.timesheet_id = timesheet.id
                created_count += 1
        
        if created_count:
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
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Info'),
                    'message': _('No new timesheet entries to create.'),
                    'type': 'info',
                    'sticky': False,
                }
            }