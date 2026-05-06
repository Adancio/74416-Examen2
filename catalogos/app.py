import os
import time
import boto3
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

AWS_REGION  = os.environ.get('AWS_REGION', 'us-east-1')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'local')
NAMESPACE   = "ESI3898K/Catalogos"

dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
cw       = boto3.client('cloudwatch', region_name=AWS_REGION)

def put_metric(metric_name, value, unit="Count", dimensions=None):
    dims = (dimensions or []) + [{'Name': 'Environment', 'Value': ENVIRONMENT}]
    try:
        cw.put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[{
                'MetricName': metric_name,
                'Dimensions': dims,
                'Value': value,
                'Unit': unit,
                'Timestamp': datetime.utcnow()
            }]
        )
    except Exception as e:
        app.logger.warning(f"CloudWatch error: {e}")

def record_request(path, method, status_code, duration_ms):
    put_metric(
        "RequestCount", 1, "Count",
        [
            {'Name': 'Path',       'Value': path},
            {'Name': 'Method',     'Value': method},
            {'Name': 'StatusCode', 'Value': str(status_code)}
        ]
    )
    put_metric(
        "ResponseTime", duration_ms, "Milliseconds",
        [{'Name': 'Path', 'Value': path}]
    )
    if status_code >= 400:
        put_metric("ErrorCount", 1, "Count",
                   [{'Name': 'StatusCode', 'Value': str(status_code)}])

@app.before_request
def start_timer():
    request._start_time = time.time()

@app.after_request
def track_metrics(response):
    duration_ms = (time.time() - request._start_time) * 1000
    record_request(request.path, request.method, response.status_code, duration_ms)
    return response


def validar_cliente(body):
    campos = ['id', 'razon_social', 'nombre_comercial', 'rfc', 'correo', 'telefono']
    for campo in campos:
        if campo not in body or str(body[campo]).strip() == '':
            return f"El campo '{campo}' es requerido y no puede estar vacío"
    if '@' not in body['correo'] or '.' not in body['correo']:
        return "El campo 'correo' no tiene un formato válido"
    if len(str(body['rfc'])) not in [12, 13]:
        return "El RFC debe tener 12 o 13 caracteres"
    return None

def validar_producto(body):
    campos = ['id', 'nombre', 'unidad_medida', 'precio_base']
    for campo in campos:
        if campo not in body or str(body[campo]).strip() == '':
            return f"El campo '{campo}' es requerido y no puede estar vacío"
    try:
        precio = float(body['precio_base'])
        if precio < 0:
            return "El campo 'precio_base' debe ser un número positivo"
    except (ValueError, TypeError):
        return "El campo 'precio_base' debe ser un número válido"
    return None

def validar_domicilio(body):
    campos = ['id', 'cliente_id', 'domicilio', 'colonia', 'municipio', 'estado', 'tipo']
    for campo in campos:
        if campo not in body or str(body[campo]).strip() == '':
            return f"El campo '{campo}' es requerido y no puede estar vacío"
    if body['tipo'].upper() not in ['FACTURACIÓN', 'ENVÍO', 'FACTURACION', 'ENVIO']:
        return "El campo 'tipo' debe ser 'FACTURACIÓN' o 'ENVÍO'"
    return None


@app.route('/clientes', methods=['GET'])
def listar_clientes():
    return jsonify(dynamodb.Table('Clientes').scan()['Items']), 200

@app.route('/clientes', methods=['POST'])
def crear_cliente():
    body = request.get_json()
    error = validar_cliente(body)
    if error:
        return jsonify({"error": error}), 400
    table = dynamodb.Table('Clientes')
    if table.get_item(Key={'id': body['id']}).get('Item'):
        return jsonify({"error": f"Ya existe un cliente con id '{body['id']}'"}), 409
    table.put_item(Item=body)
    return jsonify({"mensaje": "Cliente creado", "id": body['id']}), 201

@app.route('/clientes/<cliente_id>', methods=['GET'])
def obtener_cliente(cliente_id):
    item = dynamodb.Table('Clientes').get_item(Key={'id': cliente_id}).get('Item')
    if not item:
        return jsonify({"error": f"Cliente '{cliente_id}' no encontrado"}), 404
    return jsonify(item), 200

@app.route('/clientes/<cliente_id>', methods=['PUT'])
def actualizar_cliente(cliente_id):
    body = request.get_json()
    body['id'] = cliente_id
    error = validar_cliente(body)
    if error:
        return jsonify({"error": error}), 400
    table = dynamodb.Table('Clientes')
    if not table.get_item(Key={'id': cliente_id}).get('Item'):
        return jsonify({"error": f"Cliente '{cliente_id}' no encontrado"}), 404
    table.put_item(Item=body)
    return jsonify({"mensaje": "Cliente actualizado", "id": cliente_id}), 200

@app.route('/clientes/<cliente_id>', methods=['DELETE'])
def eliminar_cliente(cliente_id):
    table = dynamodb.Table('Clientes')
    if not table.get_item(Key={'id': cliente_id}).get('Item'):
        return jsonify({"error": f"Cliente '{cliente_id}' no encontrado"}), 404
    table.delete_item(Key={'id': cliente_id})
    return jsonify({"mensaje": "Cliente eliminado", "id": cliente_id}), 200



@app.route('/domicilios', methods=['GET'])
def listar_domicilios():
    return jsonify(dynamodb.Table('Domicilios').scan()['Items']), 200

@app.route('/domicilios', methods=['POST'])
def crear_domicilio():
    body = request.get_json()
    error = validar_domicilio(body)
    if error:
        return jsonify({"error": error}), 400
    if not dynamodb.Table('Clientes').get_item(Key={'id': body['cliente_id']}).get('Item'):
        return jsonify({"error": f"El cliente '{body['cliente_id']}' no existe"}), 404
    tipo = body['tipo'].upper()
    body['tipo'] = 'FACTURACIÓN' if tipo in ['FACTURACION', 'FACTURACIÓN'] else 'ENVÍO'
    table = dynamodb.Table('Domicilios')
    if table.get_item(Key={'id': body['id']}).get('Item'):
        return jsonify({"error": f"Ya existe un domicilio con id '{body['id']}'"}), 409
    table.put_item(Item=body)
    return jsonify({"mensaje": "Domicilio creado", "id": body['id']}), 201

@app.route('/domicilios/<domicilio_id>', methods=['GET'])
def obtener_domicilio(domicilio_id):
    item = dynamodb.Table('Domicilios').get_item(Key={'id': domicilio_id}).get('Item')
    if not item:
        return jsonify({"error": f"Domicilio '{domicilio_id}' no encontrado"}), 404
    return jsonify(item), 200

@app.route('/domicilios/<domicilio_id>', methods=['PUT'])
def actualizar_domicilio(domicilio_id):
    body = request.get_json()
    body['id'] = domicilio_id
    error = validar_domicilio(body)
    if error:
        return jsonify({"error": error}), 400
    table = dynamodb.Table('Domicilios')
    if not table.get_item(Key={'id': domicilio_id}).get('Item'):
        return jsonify({"error": f"Domicilio '{domicilio_id}' no encontrado"}), 404
    tipo = body['tipo'].upper()
    body['tipo'] = 'FACTURACIÓN' if tipo in ['FACTURACION', 'FACTURACIÓN'] else 'ENVÍO'
    table.put_item(Item=body)
    return jsonify({"mensaje": "Domicilio actualizado", "id": domicilio_id}), 200

@app.route('/domicilios/<domicilio_id>', methods=['DELETE'])
def eliminar_domicilio(domicilio_id):
    table = dynamodb.Table('Domicilios')
    if not table.get_item(Key={'id': domicilio_id}).get('Item'):
        return jsonify({"error": f"Domicilio '{domicilio_id}' no encontrado"}), 404
    table.delete_item(Key={'id': domicilio_id})
    return jsonify({"mensaje": "Domicilio eliminado", "id": domicilio_id}), 200



@app.route('/productos', methods=['GET'])
def listar_productos():
    return jsonify(dynamodb.Table('Productos').scan()['Items']), 200

@app.route('/productos', methods=['POST'])
def crear_producto():
    body = request.get_json()
    error = validar_producto(body)
    if error:
        return jsonify({"error": error}), 400
    table = dynamodb.Table('Productos')
    if table.get_item(Key={'id': body['id']}).get('Item'):
        return jsonify({"error": f"Ya existe un producto con id '{body['id']}'"}), 409
    body['precio_base'] = str(body['precio_base'])
    table.put_item(Item=body)
    return jsonify({"mensaje": "Producto creado", "id": body['id']}), 201

@app.route('/productos/<producto_id>', methods=['GET'])
def obtener_producto(producto_id):
    item = dynamodb.Table('Productos').get_item(Key={'id': producto_id}).get('Item')
    if not item:
        return jsonify({"error": f"Producto '{producto_id}' no encontrado"}), 404
    return jsonify(item), 200

@app.route('/productos/<producto_id>', methods=['PUT'])
def actualizar_producto(producto_id):
    body = request.get_json()
    body['id'] = producto_id
    error = validar_producto(body)
    if error:
        return jsonify({"error": error}), 400
    table = dynamodb.Table('Productos')
    if not table.get_item(Key={'id': producto_id}).get('Item'):
        return jsonify({"error": f"Producto '{producto_id}' no encontrado"}), 404
    body['precio_base'] = str(body['precio_base'])
    table.put_item(Item=body)
    return jsonify({"mensaje": "Producto actualizado", "id": producto_id}), 200

@app.route('/productos/<producto_id>', methods=['DELETE'])
def eliminar_producto(producto_id):
    table = dynamodb.Table('Productos')
    if not table.get_item(Key={'id': producto_id}).get('Item'):
        return jsonify({"error": f"Producto '{producto_id}' no encontrado"}), 404
    table.delete_item(key={'id': producto_id})
    return jsonify({"mensaje": "Producto eliminado", "id": producto_id}), 200


from werkzeug.wrappers import Response

def lambda_handler(event, context):
    # Construir environ WSGI desde el evento HTTP API
    environ = {
        'REQUEST_METHOD': event['requestContext']['http']['method'],
        'SCRIPT_NAME': '',
        'PATH_INFO': event['rawPath'],
        'QUERY_STRING': event.get('rawQueryString', ''),
        'CONTENT_TYPE': event['headers'].get('content-type', ''),
        'CONTENT_LENGTH': event['headers'].get('content-length', '0'),
        'SERVER_NAME': event['headers'].get('host', 'localhost'),
        'SERVER_PORT': '443',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'wsgi.version': (1, 0),
        'wsgi.url_scheme': 'https',
        'wsgi.input': None,
        'wsgi.errors': None,
        'wsgi.multithread': False,
        'wsgi.multiprocess': False,
        'wsgi.run_once': False,
    }
    
    # Agregar headers
    for header, value in event.get('headers', {}).items():
        header_key = 'HTTP_' + header.upper().replace('-', '_')
        environ[header_key] = value
    
    # Ejecutar app
    response_data = []
    status = None
    response_headers = []
    
    def start_response(status_str, headers, exc_info=None):
        nonlocal status, response_headers
        status = int(status_str.split()[0])
        response_headers = headers
        return lambda x: response_data.append(x)
    
    app_iter = app(environ, start_response)
    for data in app_iter:
        response_data.append(data)
    
    body = b''.join(response_data).decode('utf-8')
    
    return {
        'statusCode': status,
        'headers': dict(response_headers),
        'body': body
    }