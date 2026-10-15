import os
import re
import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai

app = FastAPI(title="Motor de IA & Análisis Espacial PostGIS NYC")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_CONFIG = {
    "dbname": "nyc_crime_db",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": "5432"
}

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6LAQcLIwfiqy3Pu0T5iNw3ZeUUtNYVPxneLu4urgpJe6g")
client = genai.Client(api_key=GEMINI_API_KEY)

class QuestionRequest(BaseModel):
    prompt: str

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def traducir_texto(texto):
    """Traduce términos de la base de datos de inglés a español."""
    if not isinstance(texto, str):
        return texto
    diccionario = {
        "gun": "arma de fuego",
        "knife": "cuchillo / arma blanca",
        "blunt_instrument": "objeto contundente",
        "other": "otro mecanismo",
        "unknown": "desconocido",
        "The Bronx": "El Bronx"
    }
    return diccionario.get(texto, texto)

POSTGIS_SCHEMA = """
Esquema de la base de datos PostgreSQL / PostGIS 'nyc_crime_db' (SRID 26918 - UTM NAD83 Zone 18N, distancias en metros):

1. Tabla 'nyc_homicides' (Puntos de homicidios):
   - id (integer)
   - geom (geometry(Point, 26918))
   - gcd (integer)
   - boroname (varchar: 'Brooklyn', 'The Bronx', 'Manhattan', 'Queens', 'Staten Island')
   - weapon (varchar: 'gun', 'knife', 'blunt_instrument', 'other')

2. Tabla 'nyc_subway_stations' (Estaciones de metro):
   - id (integer)
   - geom (geometry(Point, 26918))
   - name (varchar)
   - routes (varchar)
   - line (varchar)
   - boroname (varchar)

3. Tabla 'nyc_neighborhoods' (Barrios / Polígonos):
   - id (integer)
   - geom (geometry(MultiPolygon, 26918))
   - name (varchar)
   - boroname (varchar)

4. Tabla 'nyc_census_blocks' (Bloques censales / Polígonos):
   - id (integer)
   - geom (geometry(MultiPolygon, 26918))
   - blkid (varchar)
   - popn_total (integer)
   - popn_white, popn_black, popn_nativ, popn_asian, popn_other
   - boroname (varchar)

5. Tabla 'nyc_streets' (Calles / Líneas):
   - id (integer)
   - geom (geometry(MultiLineString, 26918))
   - name (varchar)
   - type (varchar)
   - boroname (varchar)

6. Tabla 'spatial_ref_sys' (Sistema de coordenadas PostGIS).
"""

def generar_sql(prompt_text):
    """Genera la consulta SQL probando los modelos de la API."""
    system_sql = f"""
    Eres un analista experto en PostGIS. Convierte la pregunta en lenguaje natural en UNA SOLA consulta SQL ejecutable.
    {POSTGIS_SCHEMA}
    REGLAS:
    1. Devuelve ÚNICAMENTE la consulta SQL sin markdown (sin ```sql).
    2. Consulta de SOLO LECTURA (SELECT).
    3. Para distancias usa ST_DWithin(geom1, geom2, metros) o ST_Distance.
    4. Para uniones espaciales usa ST_Contains, ST_Intersects, ST_Within.
    5. Distritos en inglés: 'Brooklyn', 'The Bronx', 'Manhattan', 'Queens', 'Staten Island'.
    """
    
    modelos = ["gemini-2.0-flash", "gemini-1.5-flash-latest", "gemini-1.5-flash", "gemini-2.5-flash"]
    for m in modelos:
        try:
            res = client.models.generate_content(
                model=m,
                contents=f"Pregunta: {prompt_text}\nGenera la consulta SQL:",
                config={'system_instruction': system_sql}
            )
            return res.text.strip()
        except Exception:
            continue
            
    # Respaldo con la lista de modelos de la API
    for m_info in client.models.list():
        m_name = getattr(m_info, 'name', '').replace("models/", "")
        try:
            res = client.models.generate_content(
                model=m_name,
                contents=f"Pregunta: {prompt_text}\nGenera la consulta SQL:",
                config={'system_instruction': system_sql}
            )
            return res.text.strip()
        except Exception:
            continue
            
    raise Exception("No se pudo conectar a los modelos de Gemini.")

@app.get("/")
def home():
    return {"message": "Servidor backend de Análisis Espacial activo"}

@app.post("/ask-ai")
def ask_ai(request: QuestionRequest):
    try:
        user_prompt = request.prompt.strip()
        if not user_prompt:
            return {"answer": "Por favor escribe una consulta o pregunta."}

        # 1. Generar SQL
        sql_raw = generar_sql(user_prompt)
        sql_query = re.sub(r'```sql\s*', '', sql_raw)
        sql_query = re.sub(r'```\s*', '', sql_query).strip()

        # 2. Ejecutar consulta en PostGIS
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql_query)
        
        colnames = [desc[0].lower() for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        
        cursor.close()
        conn.close()

        if not rows:
            return {"answer": "No se encontraron registros en la base de datos para esa consulta espacial."}

        # 3. Formatear la respuesta contextualizada y detallada
        
        # Caso A: 1 fila y 1 columna (Ej: un conteo total o un arma/lugar único)
        if len(rows) == 1 and len(rows[0]) == 1:
            val = rows[0][0]
            val_fmt = f"{val:,}".replace(',', '.') if isinstance(val, (int, float)) else traducir_texto(val)
            col_name = colnames[0] if colnames else ""

            if "weapon" in col_name or "arma" in col_name:
                return {"answer": f"El arma más utilizada registrada en la zona consultada es **{val_fmt}**."}
            elif "name" in col_name or "barrio" in col_name or "lugar" in col_name:
                return {"answer": f"El lugar con mayor registro según tu consulta es **{val_fmt}**."}
            elif "count" in col_name or "total" in col_name or isinstance(val, (int, float)):
                return {"answer": f"El total registrado para la consulta realizada es de **{val_fmt}** casos."}
            else:
                return {"answer": f"El resultado obtenido para tu consulta corresponde a: **{val_fmt}**."}

        # Caso B: 1 fila y 2 columnas (Ej: Nombre/Arma + Cantidad)
        elif len(rows) == 1 and len(rows[0]) == 2:
            nom = traducir_texto(rows[0][0])
            val = rows[0][1]
            val_fmt = f"{val:,}".replace(',', '.') if isinstance(val, (int, float)) else traducir_texto(val)
            col0 = colnames[0] if len(colnames) > 0 else ""

            if "weapon" in col0 or "arma" in col0:
                return {"answer": f"El arma más usada registrada en este sector es **{nom}**, con un total de **{val_fmt}** homicidios."}
            elif "name" in col0 or "barrio" in col0 or "lugar" in col0:
                return {"answer": f"El lugar o zona identificada con mayor nivel es **{nom}**, registrando un total de **{val_fmt}** eventos."}
            else:
                return {"answer": f"El elemento principal identificado es **{nom}** con un valor de **{val_fmt}**."}

        # Caso C: Múltiples filas (Listados o Rankings)
        else:
            lineas = ["**Resultados detallados del análisis espacial:**"]
            for row in rows[:10]:
                items = [f"{item:,}".replace(',', '.') if isinstance(item, (int, float)) else str(traducir_texto(item)) for item in row]
                lineas.append("- " + " | ".join(items))
            return {"answer": "\n".join(lineas)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en la consulta: {str(e)}")

    from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI()

@app.get("/")
def read_root():
    return FileResponse("app.html")  # Asegúrate de colocar el nombre exacto de tu archivo HTML


from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI()

# Middleware para omitir la advertencia de ngrok
class NgrokBypassMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["ngrok-skip-browser-warning"] = "true"
        return response

app.add_middleware(NgrokBypassMiddleware)

@app.get("/")
def read_root():
    return FileResponse("app.html")