# -*- coding: utf-8 -*-
{
    'name': "Shipment Extend",
    'summary': """""",
    'description': """""",
    'author': "My Company",
    'website': "https://www.yourcompany.com",
    'category': 'Uncategorized',
    'version': '0.1',
    # any module necessary for this one to work correctly
    'depends': ['base', 'purchase', 'stock'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
        'data/sequence.xml',
    ],
}
