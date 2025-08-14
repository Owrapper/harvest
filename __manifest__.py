{
    'name': 'Harvest Time Tracking Integration',
    'version': '16.0.1.0.0',
    'category': 'Human Resources/Timesheets',
    'summary': 'Integration with Harvest Time Tracking',
    'description': """
        Harvest Time Tracking Integration
        ==================================
        This module provides integration with Harvest time tracking service:
        - Sync time entries from Harvest to Odoo
        - Map Harvest projects to Odoo projects
        - Import Harvest users as employees
        - Generate timesheets from Harvest data
        - API configuration and authentication
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'depends': ['base', 'hr', 'hr_timesheet', 'project', 'sale', 'account'],
    'data': [
        'security/harvest_security.xml',
        'security/ir.model.access.csv',
        'views/harvest_config_views.xml',
        'views/harvest_time_entry_views.xml',
        'views/harvest_project_views.xml',
        'views/sale_order_views.xml',
        'views/harvest_menu.xml',
        'wizard/harvest_timesheet_wizard_views.xml',
        'data/ir_cron.xml',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
