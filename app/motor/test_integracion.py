import copy
import sqlite3

import pandas as pd
import pytest

import integrar_alertas as ia
import motor_reglas as m

XLSX = "Dataset_Base_Fraude_Interno.xlsx"
DDL = """
CREATE TABLE niveles_riesgo(nivel_riesgo TEXT PRIMARY KEY, orden INTEGER, descripcion TEXT);
CREATE TABLE estados_alerta(estado TEXT PRIMARY KEY, orden INTEGER, es_final INTEGER, descripcion TEXT);
CREATE TABLE tipos_evento(id_tipo_evento TEXT PRIMARY KEY, tipo_fuente TEXT, descripcion TEXT);
CREATE TABLE roles(rol_id TEXT PRIMARY KEY, nombre_rol TEXT, nivel_base INTEGER, descripcion TEXT);
CREATE TABLE usuarios(id_usuario TEXT PRIMARY KEY, rol_id TEXT, nivel_privilegio INTEGER, estado TEXT);
CREATE TABLE cuentas(id_cuenta TEXT PRIMARY KEY, tipo_cuenta TEXT, limite_operacion REAL, umbral_operacion REAL, estado TEXT);
CREATE TABLE recursos_modulos(id_recurso TEXT PRIMARY KEY, nombre_recurso TEXT, nivel_sensibilidad INTEGER, descripcion TEXT);
CREATE TABLE eventos(id_evento TEXT PRIMARY KEY, tipo_fuente TEXT, id_tipo_evento TEXT, id_usuario TEXT, fecha_hora TEXT);
CREATE TABLE accesos(id_evento TEXT PRIMARY KEY, tipo_fuente TEXT, id_recurso TEXT, resultado TEXT);
CREATE TABLE eventos_seguridad(id_evento TEXT PRIMARY KEY, tipo_fuente TEXT, id_cuenta TEXT, id_usuario_autorizador TEXT,
  valor_anterior TEXT, valor_nuevo TEXT, detalle_sintetico TEXT);
CREATE TABLE operaciones(id_evento TEXT PRIMARY KEY, tipo_fuente TEXT, id_cuenta TEXT, id_tipo_operacion TEXT, monto REAL);
CREATE TABLE reglas(id_regla TEXT PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT NOT NULL, clave_correlacion TEXT NOT NULL,
  ventana_minutos INTEGER NOT NULL CHECK(ventana_minutos>0), criticidad TEXT NOT NULL REFERENCES niveles_riesgo(nivel_riesgo),
  activa INTEGER NOT NULL, version INTEGER NOT NULL);
CREATE TABLE regla_pasos(id_regla TEXT REFERENCES reglas(id_regla), orden_paso INTEGER, id_tipo_evento TEXT REFERENCES tipos_evento(id_tipo_evento),
  condicion TEXT, PRIMARY KEY(id_regla, orden_paso));
CREATE TABLE alertas(id_alerta TEXT PRIMARY KEY, id_regla TEXT NOT NULL REFERENCES reglas(id_regla), version_regla INTEGER NOT NULL,
  id_usuario TEXT NOT NULL REFERENCES usuarios(id_usuario), id_cuenta TEXT REFERENCES cuentas(id_cuenta),
  nivel_riesgo TEXT NOT NULL REFERENCES niveles_riesgo(nivel_riesgo), fecha_hora TEXT NOT NULL,
  estado TEXT NOT NULL REFERENCES estados_alerta(estado), explicacion TEXT NOT NULL, huella_secuencia TEXT NOT NULL,
  UNIQUE(id_regla, huella_secuencia));
CREATE TABLE alerta_eventos(id_alerta TEXT REFERENCES alertas(id_alerta), id_evento TEXT REFERENCES eventos(id_evento),
  orden_secuencia INTEGER, rol_evento TEXT, PRIMARY KEY(id_alerta, id_evento));
"""
ORDEN = ["niveles_riesgo", "estados_alerta", "tipos_evento", "roles", "usuarios", "cuentas", "recursos_modulos",
         "eventos", "accesos", "eventos_seguridad", "operaciones", "reglas", "regla_pasos", "alertas", "alerta_eventos"]


@pytest.fixture
def db(tmp_path):
    ruta = tmp_path / "base.db"
    con = sqlite3.connect(ruta)
    con.executescript(DDL)
    hojas = pd.read_excel(XLSX, sheet_name=ORDEN)
    for n in ORDEN:
        df = hojas[n].copy()
        for c in df.columns:
            if str(df[c].dtype).startswith("datetime"):
                df[c] = df[c].dt.strftime("%Y-%m-%d %H:%M:%S")
        df.to_sql(n, con, if_exists="append", index=False)
    con.commit()
    con.close()
    return str(ruta)


def consulta(ruta, sql):
    con = sqlite3.connect(ruta)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_primera_integracion(db):
    antes = consulta(db, "SELECT id_alerta, estado FROM alertas ORDER BY 1")
    informe, nuevas = ia.integrar(db)
    assert sorted(a[1] for a in nuevas) == ["R-05", "R-11"]
    assert [x[0] for x in consulta(db, "SELECT id_regla FROM reglas ORDER BY 1")] == ["R-03", "R-05", "R-07", "R-09", "R-11"]
    assert consulta(db, "SELECT COUNT(*) FROM regla_pasos")[0][0] == 8
    assert consulta(db, "SELECT id_alerta, estado FROM alertas WHERE id_alerta IN ('AL-0001','AL-0002','AL-0003','AL-0004') ORDER BY 1") == antes
    assert all(e == "ABIERTA" for (e,) in consulta(db, "SELECT estado FROM alertas WHERE id_alerta > 'AL-0004'"))
    assert any("R-07: sin cambios" in l for l in informe)


def test_idempotente(db):
    ia.integrar(db)
    informe, nuevas = ia.integrar(db)
    assert nuevas == [] and all("sin cambios" in l for l in informe)
    assert consulta(db, "SELECT COUNT(*) FROM alertas")[0][0] == 6


def test_simular_no_persiste(db):
    _, nuevas = ia.integrar(db, simular=True)
    assert len(nuevas) == 2
    assert consulta(db, "SELECT COUNT(*) FROM alertas")[0][0] == 4
    assert consulta(db, "SELECT COUNT(*) FROM reglas")[0][0] == 1


def test_cambio_sin_subir_version_se_rechaza(db):
    ia.integrar(db)
    cat = copy.deepcopy(m.cargar_catalogo())
    for r in cat["reglas"]:
        if r["id_regla"] == "R-07":
            r["ventana_minutos"] = 45
    with pytest.raises(ia.CambioSinVersion):
        ia.integrar(db, catalogo=cat)
    assert consulta(db, "SELECT ventana_minutos FROM reglas WHERE id_regla='R-07'")[0][0] == 30


def test_cambio_con_version_se_aplica(db):
    ia.integrar(db)
    cat = copy.deepcopy(m.cargar_catalogo())
    for r in cat["reglas"]:
        if r["id_regla"] == "R-07":
            r["ventana_minutos"] = 45
            r["version"] = 2
    informe, _ = ia.integrar(db, catalogo=cat)
    assert any("R-07: versión 1 a 2" in l for l in informe)
    assert consulta(db, "SELECT ventana_minutos, version FROM reglas WHERE id_regla='R-07'") == [(45, 2)]


def test_eventos_de_alertas_nuevas_ordenados(db):
    ia.integrar(db)
    filas = consulta(db, """SELECT ae.id_alerta, e.fecha_hora FROM alerta_eventos ae
                            JOIN eventos e USING(id_evento) ORDER BY ae.id_alerta, ae.orden_secuencia""")
    por = {}
    for a, t in filas:
        por.setdefault(a, []).append(t)
    assert all(v == sorted(v) for v in por.values())
