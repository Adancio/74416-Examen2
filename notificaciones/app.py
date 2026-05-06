import os
import time
import boto3
from datetime import datetime
from flask import Flask, request, jsonify, redirect

app = Flask(__name__)

AWS_REGION    = os.environ.get('AWS_REGION', 'us-east-1')
BUCKET_NAME   = os.environ.get('BUCKET_NAME')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
ENVIRONMENT   = os.environ.get('ENVIRONMENT', 'local')
NAMESPACE     = "ESI3898K/Notificaciones"

sns = boto3.client('sns',        region_name=AWS_REGION)
s3  = boto3.client('s3',         region_name=AWS_REGION)
cw  = boto3.client('cloudwatch', region_name=AWS_REGION)

# ─────────────────────────────────────────────
# MÉTRICAS
# ─────────────────────────────────────────────

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

# ─────────────────────────────────────────────
# RUTAS
# ─────────────────────────────────────────────

@app.route('/notificar', methods=['POST'])
def notificar():
    data  = request.get_json()
    rfc   = data.get('rfc')
    folio = data.get('folio')
    link  = data.get('link')

    if not rfc or not folio or not link:
        return jsonify({"error": "Se requieren 'rfc', 'folio' y 'link'"}), 400

    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=f"Su nota ha sido generada. Descargue aquí: {link}",
            Subject=f"Nota {folio}"
        )
        put_metric("NotificacionesEnviadas", 1, "Count")
        return jsonify({"mensaje": "Notificación enviada", "folio": folio}), 200
    except Exception as e:
        put_metric("NotificacionesError", 1, "Count")
        return jsonify({"error": str(e)}), 500

@app.route('/descarga', methods=['GET'])
def descarga():
    rfc   = request.args.get('rfc')
    folio = request.args.get('folio')

    if not rfc or not folio:
        return jsonify({"error": "Se requieren 'rfc' y 'folio'"}), 400

    s_key = f"{rfc}/{folio}.pdf"

    try:
        res  = s3.head_object(Bucket=BUCKET_NAME, Key=s_key)
        meta = res['Metadata']

        meta['nota-descargada'] = 'true'
        actual = int(meta.get('veces-enviado', 1))
        meta['veces-enviado'] = str(actual + 1)
        meta['hora-envio']    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        s3.copy_object(
            Bucket=BUCKET_NAME, Key=s_key,
            CopySource={'Bucket': BUCKET_NAME, 'Key': s_key},
            Metadata=meta, MetadataDirective='REPLACE'
        )

        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': s_key},
            ExpiresIn=300
        )

        put_metric("DescargasRealizadas", 1, "Count")
        return redirect(url, code=302)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "modulo": "notificaciones", "ambiente": ENVIRONMENT}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8082)
    
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

