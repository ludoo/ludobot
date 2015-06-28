import sys
import logging
import json
import requests

from flask import Flask, request, abort

from pprint import pprint as pp


APP_CONFIG = 'bot.cfg'
TOKEN = '108651160:AAHdiIVKqjOf58KkfhvkIQthoL3FSSXNCe8'
BASE_URL = 'https://api.telegram.org/bot'
GOOGLE_URL = 'http://finance.google.com/finance/info?client=ig&q='
DEBUG = False


### logging

logging.basicConfig(
    format="%(asctime)-15s %(levelname)s %(message)s",
    level=logging.INFO if not DEBUG else logging.DEBUG
)
logger = logging.getLogger()


### flask app init

app = Flask(__name__)
try:
    app.config.from_pyfile(APP_CONFIG)
except IOError, e:
    logger.critical("No configuration file found: %s" % e)
    raise SystemExit(1)


if not app.config.get('TOKEN'):
    logger.critical("No 'TOKEN' key found in app.config")
    raise SystemExit(1)


### internal functions

def _telegram_api_url(method_name):
    return ''.join((
        BASE_URL, app.config['TOKEN'], '/', method_name, '/'
    ))


def _google_finance_request(*args):
    
    if len(args) == 0:
        raise ValueError('missing stock symbol')
    
    try:
        response = requests.get(GOOGLE_URL + ','.join(args))
    except requests.exceptions.RequestException, e:
        raise ValueError('connection error: %s' % e)
    
    if reponse.status_code != 200:
        raise ValueError('response error, status code %s' % response.status_code)
    
    # fix the invalid response body
    result = response.text.replace('\n', '')
    if result.startswith('// '):
        result = result[3:]
    
    try:
        result = json.loads(result)
    except ValueError, e:
        raise ValueError('error decoding result: %s' % e)
    
    if len(result) == 0:
        raise ValueError('no results')
        
    return result


# https://api.telegram.org/bot108651160:AAHdiIVKqjOf58KkfhvkIQthoL3FSSXNCe8/setwebhook?url=https://bots.qix.it/ludo/


def bot_command(f, *args):
    
    if callable(f):
        f._command_description = None
        f._command_args = []
        return f
    
    def wrapper(func):
        func._command_description = f
        func._command_args = args
        return func
    
    return wrapper


def get_commands():
    mod = sys.modules[__name__]
    for name in dir(mod):
        if not name.startswith('_do'):
            continue
        func = getattr(mod, name)
        if not callable(func) or not hasattr(func, '_command_description'):
            continue
        yield name, func


@bot_command
def _do_help(*args):
    
    for name, func in sorted(get_commands()):
        
        if not func._command_description:
            continue
        
        buffer.append('/%s%s - %s' % (
            func.func_name[4:],
            '' if not func._command_args else (' ' + ', '.join(func._command_args)),
            func._command_description
        ))
    
    buffer.append('/help - this command')
    
    return '\n'.join(buffer)


@bot_command('3-day chart for STOCK', 'STOCK')
def _do_chart(*args):

    if len(args) == 0:
        return 'missing stock symbol'
    
    return 'http://chart.finance.yahoo.com/z?s=%s&t=3d&q=c&l=on&z=l' % args[0]


@bot_command('main indexes')
def _do_indexes(*args):
    
    mapping = {
        '.INX': 'S&P 500',
        'NDX': 'Nasdaq 100',
        '.IXIC': 'Nasdaq Composite',
        'FTSEMIB': 'FTSE MIB',
    }
    
    try:
        result = _google_finance_request(*mapping.keys())
    except ValueError, e:
        return e.args[0]
    
    buffer = []
    
    for r in result:
        r['verbose_name'] = mapping.get(r['l'], r['l'])
        buffer.append(
            '%(verbose_name)s %(cp)s%% %(lt)s' % r # \next hours %(el)s %(ecp)s%% %(elt)s
        )
    
    return '\n'.join(buffer)


@bot_command('quotes for STOCK [STOCK...]', 'STOCK [STOCK...]')
def _do_quote(*args):
    
    # http://www.quora.com/What-do-the-following-attributes-returned-in-a-JSON-array-from-the-Google-Finance-API-mean
    
    try:
        result = _google_finance_request(*args)
    except ValueError, e:
        return e.args[0]
        
    buffer = []
    
    for r in result:
        logger.debug(', '.join(r.keys()))
        buffer.append(
            '%(e)s:%(t)s %(l)s %(cp)s%% %(lt)s' % r # \next hours %(el)s %(ecp)s%% %(elt)s
        )
    
    return '\n'.join(buffer)
    

@app.route("/ludo/", methods=['POST'])
def ludobot():
    """
    {
        "update_id":303455743,
        "message":{
            "message_id":7,
            "from":{
                "id":10274692,"first_name":"Ludovico","last_name":"Magnocavallo","username":"ludoo"
            },
            "chat":{
                "id":10274692,"first_name":"Ludovico","last_name":"Magnocavallo","username":"ludoo"
            },
            "date":1435336725,
            "text":"abc abc"
        }
    }
    
    {
        u'message': {
            u'chat': {u'id': -31394780, u'title': u'test'},
            u'date': 1435344346,
            u'from': {
                u'first_name': u'Ludovico',
                u'id': 10274692,
                u'last_name': u'Magnocavallo',
                u'username': u'ludoo'
            },
            u'message_id': 23,
            u'new_chat_participant': {
                u'first_name': u'ludo',
                u'id': 108651160,
                u'username': u'LudoBot'
            }
        },
        u'update_id': 303455750
    }

    """
    
    try:
        data = json.loads(request.data)
    except ValueError, e:
        logger.critical("error decoding webhook json data: %s" % e)
        logger.debug(request.data)
        abort(400)

    if DEBUG:
        print >>sys.stderr, '\n\n --- webhook received --- \n'
        pp(data, stream=sys.stderr)
    
    text = data['message'].get('text')
    
    if text is None:
        return ''
    
    if isinstance(text, unicode):
        text = text.encode('utf-8')
    
    if text.startswith('/'):
        
        tokens = text[1:].split()
        
        func = globals().get('_do_%s' % tokens[0])
        if func and callable(func) and hasattr(func, '_command_description'):
            output = func(*tokens[1:])
        else:
            output = 'Unsupported command'
            
    else:
        
        output = 'What are you trying to say?'
            
    
    try:
        response = requests.get(_telegram_api_url('sendMessage'), params={
            'chat_id':data['message']['chat']['id']),
            'text': output
        })
    except requests.exceptions.RequestException, e:
        logger.critical("Error sending response: %s" % e)
        
    if response.status_code != 200:
        logger.critical("Error in server response to command, status code %s" % response.status_code)

    if DEBUG:
        print >>sys.stderr, "\n\n --- client received -- \n\n"
        pp(response, stream=sys.stderr)
    
    
    return ""


if __name__ == "__main__":
    app.run(port=9002, debug=True)
