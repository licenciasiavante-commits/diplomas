import os
import sys
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file, make_response

# Initialize Flask application
app = Flask(__name__)

# File paths for data storage
current_dir = os.path.dirname(os.path.abspath(__file__))
state_file = os.path.join(current_dir, "state.json")
file_status_file = os.path.join(current_dir, "file_status.json")

# Retrieve admin password
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    try:
        import config
        ADMIN_PASSWORD = config.ADMIN_PASSWORD
    except ImportError:
        ADMIN_PASSWORD = "sas_dentistas_2026" # Default fallback

# Helper to read JSON files safely
def read_json_file(filepath):
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading file {filepath}: {e}")
        return {}

# Helper to write JSON files safely
def write_json_file(filepath, data):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error writing file {filepath}: {e}")
        return False

# Merge local file scan status with manual overrides
def get_merged_data():
    file_status = read_json_file(file_status_file)
    state = read_json_file(state_file)
    
    # If no file status has been synced yet, we return empty
    if not file_status:
        return {}
        
    merged = {}
    for dip_id, dip_data in file_status.items():
        topics_list = []
        for t in dip_data.get("topics", []):
            t_num_str = str(t["num"])
            # Get overrides from state
            override = state.get(dip_id, {}).get(t_num_str, {})
            
            t_merged = {
                "num": t["num"],
                "title": t["title"],
                "has_bib": override.get("has_bib", False),
                "has_master": override.get("has_master", False),
                "master_registered_at": override.get("master_registered_at", None),
                "is_validated": override.get("is_validated", False),
                "validated_at": override.get("validated_at", None)
            }
            topics_list.append(t_merged)
            
        merged[dip_id] = {
            "name": dip_data["name"],
            "topics": topics_list
        }
    return merged

# API Endpoints
@app.route('/')
def index():
    return render_template("index.html")

@app.route('/api/data', methods=['GET'])
def api_get_data():
    data = get_merged_data()
    return jsonify({
        "success": True,
        "data": data,
        "is_configured": len(data) > 0
    })

@app.route('/api/login', methods=['POST'])
def api_login():
    req_data = request.json or {}
    password = req_data.get("password")
    
    if password == ADMIN_PASSWORD:
        return jsonify({"success": True, "message": "Contraseña válida."})
    else:
        return jsonify({"success": False, "message": "Contraseña incorrecta."})

@app.route('/api/sync', methods=['POST'])
def api_sync():
    req_data = request.json or {}
    password = req_data.get("password")
    scanned_data = req_data.get("data")
    
    if password != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "Contraseña incorrecta para sincronizar."}), 401
        
    if not scanned_data:
        return jsonify({"success": False, "message": "Datos de sincronización vacíos."}), 400
        
    # Save the file status
    if write_json_file(file_status_file, scanned_data):
        return jsonify({"success": True, "message": "Datos de archivos locales guardados correctamente en el servidor."})
    else:
        return jsonify({"success": False, "message": "Error al escribir el archivo de estado en el servidor."}), 500

@app.route('/api/update', methods=['POST'])
def api_update():
    req_data = request.json or {}
    password = req_data.get("password")
    dip_id = req_data.get("dip_id")
    topic_num = str(req_data.get("topic_num"))
    field = request.json.get("field") # 'has_master', 'is_validated', or 'has_bib'
    value = bool(req_data.get("value"))
    
    if password != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "No autorizado. Contraseña incorrecta."}), 401
        
    if not dip_id or not topic_num or not field:
        return jsonify({"success": False, "message": "Parámetros insuficientes."}), 400
        
    state = read_json_file(state_file)
    
    # Initialize dictionary structure
    if dip_id not in state:
        state[dip_id] = {}
    if topic_num not in state[dip_id]:
        state[dip_id][topic_num] = {
            "has_master": False,
            "master_registered_at": None,
            "is_validated": False,
            "validated_at": None,
            "has_bib": False
        }
        
    # Update field and register timestamp
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    if field == "has_master":
        state[dip_id][topic_num]["has_master"] = value
        state[dip_id][topic_num]["master_registered_at"] = now_str if value else None
            
    elif field == "is_validated":
        state[dip_id][topic_num]["is_validated"] = value
        state[dip_id][topic_num]["validated_at"] = now_str if value else None
        
    elif field == "has_bib":
        state[dip_id][topic_num]["has_bib"] = value
        
    if write_json_file(state_file, state):
        return jsonify({
            "success": True, 
            "message": "Actualización guardada correctamente.",
            "updated_topic": state[dip_id][topic_num]
        })
    else:
        return jsonify({"success": False, "message": "Error al persistir los cambios."}), 500

@app.route('/api/export', methods=['POST'])
def api_export():
    # Exports a self-contained, read-only HTML file with the CURRENT data embedded.
    data = get_merged_data()
    
    # Read the template
    template_path = os.path.join(current_dir, "templates", "index.html")
    if not os.path.exists(template_path):
        return jsonify({"success": False, "message": "No se encontró la plantilla HTML para exportar."}), 500
        
    with open(template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
        
    # We will inject the data as a global JS variable right before the script tag
    data_json_str = json.dumps(data, ensure_ascii=False)
    injection = f"\n<script>\n  window.EXPORTED_DASHBOARD_DATA = {data_json_str};\n</script>\n"
    
    # Insert before the closing body tag or head tag
    if "</body>" in html_content:
        html_content = html_content.replace("</body>", f"{injection}</body>")
    else:
        html_content += injection
        
    # Return as an downloadable attachment
    response = make_response(html_content)
    response.headers["Content-Disposition"] = "attachment; filename=dashboard_publico.html"
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response

if __name__ == '__main__':
    # Default port for Render/PythonAnywhere is usually determined by environment variables
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
