from flask import Blueprint
from app.web import api, page
bp = Blueprint('m8_stoppage', __name__)

@bp.get('/m8_stoppage')
def screen():
    return page(8, 'm8_stoppage.html')

@bp.route('/api/m8_stoppage', methods=['GET', 'POST'])
def preview():
    return api(8)
