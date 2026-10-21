import re
import json
import requests
from pathlib import Path
import pandas as pd

# ========== 1. Intentar descargar datos desde Nextcloud ==========
SHARE_URL = "https://files.minsa.gob.pe/s/RjeWiJt2wX3pMdG"
TOKEN = SHARE_URL.split("/s/")[1]
WEBDAV_BASE = "https://files.minsa.gob.pe/public.php/webdav/"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}

def descargar_datos():
    """Intenta descargar el CSV más reciente desde la carpeta pública de Nextcloud."""
    try:
        # Listar archivos
        resp = requests.request("PROPFIND", WEBDAV_BASE, 
                                auth=(TOKEN, ""), headers={**HEADERS, "Depth": "1"}, timeout=30)
        resp.raise_for_status()
        # Extraer nombres de archivos CSV (simplificado)
        import xml.etree.ElementTree as ET
        ns = {"d": "DAV:"}
        root = ET.fromstring(resp.content)
        csvs = []
        for r in root.findall("d:response", ns):
            href = r.find("d:href", ns)
            if href is not None and href.text and href.text.lower().endswith(".csv"):
                csvs.append(href.text.strip())
        if not csvs:
            raise FileNotFoundError("No hay CSV en la carpeta pública")
        # Tomar el que tenga fecha más reciente en el nombre (asumiendo formato DDMMYYYY)
        csvs.sort(reverse=True)
        archivo = csvs[0]
        # Descargar
        url = WEBDAV_BASE.rstrip("/") + archivo
        r = requests.get(url, auth=(TOKEN, ""), headers=HEADERS, timeout=120)
        r.raise_for_status()
        Path("datos_raw.csv").write_bytes(r.content)
        print(f"Descargado: {archivo}")
        return Path("datos_raw.csv")
    except Exception as e:
        print(f"Descarga automática fallida: {e}")
        print("Se esperaba un archivo datos_raw.csv subido manualmente.")
        # Si existe un archivo subido manualmente, usarlo
        if Path("datos_raw.csv").exists():
            return Path("datos_raw.csv")
        raise

# ========== 2. Cargar y limpiar (tu lógica original) ==========
def limpiar_texto(x):
    if pd.isna(x): return ""
    x = str(x).strip().upper()
    for a, b in [("Á","A"),("É","E"),("Í","I"),("Ó","O"),("Ú","U")]:
        x = x.replace(a, b)
    return re.sub(r"[^A-Z0-9]+", "_", x).strip("_")

def analizar(ruta_csv):
    df = pd.read_csv(ruta_csv, dtype=str, sep=None, engine="python")
    df.columns = [re.sub(r"[^A-Z0-9]+", "_", c.strip().upper()) for c in df.columns]
    
    # Limpiar geografía
    for col in ["DEPARTAMENTO_DOMICILIO","PROVINCIA_DOMICILIO","DISTRITO_DOMICILIO",
                "DEPARTAMENTO_FALLECIMIENTO","PROVINCIA_FALLECIMIENTO","DISTRITO_FALLECIMIENTO"]:
        if col in df.columns:
            df[col] = df[col].apply(limpiar_texto)
        else:
            df[col] = ""
    
    # Filtrar Huaral
    mask_dom = (df["DEPARTAMENTO_DOMICILIO"]=="LIMA") & (df["PROVINCIA_DOMICILIO"]=="HUARAL")
    mask_fall = (df["DEPARTAMENTO_FALLECIMIENTO"]=="LIMA") & (df["PROVINCIA_FALLECIMIENTO"]=="HUARAL")
    prov = df[mask_dom | mask_fall].copy()
    
    if prov.empty:
        return {"error": "No hay datos de Huaral", "actualizado": pd.Timestamp.now().isoformat()}
    
    # Detección PAF (simplificada)
    cod_cols = [f"CAUSA_{l}_CIEX" for l in "ABCDEF"]
    for c in cod_cols:
        prov[c] = prov[c].fillna("").str.upper()
    codigos = prov[cod_cols].agg("|".join, axis=1)
    
    prov["PAF_HOMICIDIO"] = codigos.str.contains(r"X9[345]", regex=True, na=False)
    prov["PAF_SUICIDIO"] = codigos.str.contains(r"X7[234]", regex=True, na=False)
    prov["PAF_ACCIDENTE"] = codigos.str.contains(r"W3[234]", regex=True, na=False)
    prov["PAF_TOTAL"] = prov[["PAF_HOMICIDIO","PAF_SUICIDIO","PAF_ACCIDENTE"]].any(axis=1)
    
    prov["DOM_HUARAL"] = mask_dom.loc[prov.index] & (prov["DISTRITO_DOMICILIO"]=="HUARAL")
    prov["FALL_HUARAL"] = mask_fall.loc[prov.index] & (prov["DISTRITO_FALLECIMIENTO"]=="HUARAL")
    
    # Generar resumen
    resumen = []
    for nombre, mask in [("Residentes en Huaral", prov["DOM_HUARAL"]),
                         ("Fallecidos en Huaral", prov["FALL_HUARAL"])]:
        sub = prov[mask]
        if len(sub) > 0:
            resumen.append({
                "ambito": nombre,
                "total_defunciones": int(len(sub)),
                "paf_homicidio": int(sub["PAF_HOMICIDIO"].sum()),
                "paf_total": int(sub["PAF_TOTAL"].sum())
            })
    
    return {
        "actualizado": pd.Timestamp.now().isoformat(),
        "resumen": resumen,
        "total_provincia": int(len(prov))
    }

# ========== 3. Ejecutar y guardar JSON ==========
if __name__ == "__main__":
    ruta = descargar_datos()
    resultado = analizar(ruta)
    Path("datos.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
    print("datos.json generado")
