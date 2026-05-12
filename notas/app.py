import os
import time
import boto3
from datetime import datetime
from flask import Flask, request, jsonify
from fpdf import FPDF

app = Flask(__name__)

AWS_REGION  = os.environ.get('AWS_REGION', 'us-east-1')
BUCKET_NAME = os.environ.get('BUCKET_NAME')
INVOKE_URL  = os.environ.get('INVOKE_URL')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'local')
NAMESPACE   = "ESI3898K/Notas"

dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
s3       = boto3.client('s3',         region_name=AWS_REGION)
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


def generar_pdf(nota, detalles):
    folio = nota['id']
    rfc   = nota['rfc']
    local_path = f"/tmp/{folio}.pdf"

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', size=16)
    pdf.cell(200, 10, txt=f"NOTA DE VENTA: {folio}", ln=True, align='C')

    pdf.set_font("Arial", size=12)
    pdf.ln(10)
    pdf.cell(200, 10, txt=f"RFC Cliente: {rfc}", ln=True)
    pdf.cell(200, 10, txt=f"Razon Social: {nota.get('razon_social', 'N/A')}", ln=True)
    pdf.ln(5)

    pdf.cell(40,  10, "Cant.",    1)
    pdf.cell(100, 10, "Producto", 1)
    pdf.cell(50,  10, "Importe",  1, ln=True)

    for d in detalles:
        pdf.cell(40,  10, str(d['cantidad']),    1)
        pdf.cell(100, 10, str(d['id_producto']), 1)
        pdf.cell(50,  10, f"${d['importe']}",    1, ln=True)

    pdf.output(local_path)
    return local_path

def subir_pdf_a_s3(local_path, rfc, folio):
    s_key = f"{rfc}/{folio}.pdf"
    meta  = {
        'hora-envio':      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'nota-descargada': 'false',
        'veces-enviado':   '1'
    }
    with open(local_path, "rb") as f:
        s3.put_object(
            Bucket=BUCKET_NAME, Key=s_key,
            Body=f, Metadata=meta, ContentType='application/pdf'
        )
    return s_key


@app.route('/notas', methods=['GET'])
def listar_notas():
    return jsonify(dynamodb.Table('NotasVenta').scan()['Items']), 200

@app.route('/notas', methods=['POST'])
def crear_nota():
    data     = request.get_json()
    nota     = data.get('nota', {})
    detalles = data.get('contenido', [])

    if not nota or not detalles:
        return jsonify({"error": "Se requieren 'nota' y 'contenido'"}), 400

    campos = ['id', 'rfc', 'razon_social', 'total']
    for campo in campos:
        if campo not in nota or str(nota[campo]).strip() == '':
            return jsonify({"error": f"El campo '{campo}' es requerido"}), 400

    if len(str(nota['rfc'])) not in [12, 13]:
        return jsonify({"error": "El RFC debe tener 12 o 13 caracteres"}), 400

    dynamodb.Table('NotasVenta').put_item(Item=nota)

    for item in detalles:
        dynamodb.Table('ContenidoNotas').put_item(Item=item)

    t_pdf      = time.time()
    local_path = generar_pdf(nota, detalles)
    s_key      = subir_pdf_a_s3(local_path, nota['rfc'], nota['id'])
    pdf_ms     = (time.time() - t_pdf) * 1000

    put_metric("PDFGenerationTime", pdf_ms, "Milliseconds")
    put_metric("NotasCreadas", 1, "Count")

    link = f"{INVOKE_URL}/descarga?rfc={nota['rfc']}&folio={nota['id']}"

    return jsonify({
        "mensaje": "Nota creada correctamente",
        "folio":   nota['id'],
        "pdf_key": s_key,
        "link_descarga": link
    }), 201

@app.route('/notas/<folio>', methods=['GET'])
def obtener_nota(folio):
    item = dynamodb.Table('NotasVenta').get_item(Key={'id': folio}).get('Item')
    if not item:
        return jsonify({"error": f"Nota '{folio}' no encontrada"}), 404
    return jsonify(item), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "modulo": "notas", "ambiente": ENVIRONMENT}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081)

import io
from werkzeug.wrappers import Response

def lambda_handler(event, context):
    body = event.get('body', '') or ''
    if event.get('isBase64Encoded', False):
        import base64
        body = base64.b64decode(body)
    else:
        body = body.encode('utf-8')

    environ = {
        'REQUEST_METHOD': event['requestContext']['http']['method'],
        'SCRIPT_NAME': '',
        'PATH_INFO': event['rawPath'],
        'QUERY_STRING': event.get('rawQueryString', ''),
        'CONTENT_TYPE': event.get('headers', {}).get('content-type', ''),
        'CONTENT_LENGTH': str(len(body)),
        'SERVER_NAME': event.get('headers', {}).get('host', 'localhost'),
        'SERVER_PORT': '443',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'wsgi.version': (1, 0),
        'wsgi.url_scheme': 'https',
        'wsgi.input': io.BytesIO(body),
        'wsgi.errors': io.StringIO(),
        'wsgi.multithread': False,
        'wsgi.multiprocess': False,
        'wsgi.run_once': False,
    }

    for header, value in event.get('headers', {}).items():
        key = 'HTTP_' + header.upper().replace('-', '_')
        environ[key] = value

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

    body_out = b''.join(response_data).decode('utf-8')

    return {
        'statusCode': status,
        'headers': dict(response_headers),
        'body': body_out
    }