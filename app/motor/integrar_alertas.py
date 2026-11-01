"""Integra el motor de reglas con la base fraude_interno_base.db.

1. Sincroniza el catálogo JSON con las tablas reglas y regla_pasos, con control de cambios:
   una regla cambiada exige subir su versión.
2. Ejecuta el motor y escribe las señales nuevas en alertas y alerta_eventos, estado ABIERTA.
   Las señales ya existentes, identificadas por regla y huella de secuencia, no se tocan.

Uso: python integrar_alertas.py fraude_interno_base.db [--simular]
"""
import sqlite3
import sys

import pandas as pd

import motor_reglas as m

CAMPOS_REGLA = ["nombre", "descripcion", "clave_correlacion", "ventana_minutos", "criticidad", "activa", "version"]


class CambioSinVersion(Exception):
    pass


def _fila_catalogo(r):
    return {"nombre": r["nombre"], "descripcion": r["descripcion"], "clave_correlacion": r["correlacion"],
            "ventana_minutos": r["ventana_minutos"], "criticidad": r["criticidad"],
            "activa": int(r["activa"]), "version": r["version"]}


def sincronizar_reglas(con, catalogo):
    informe = []
    for r in catalogo["reglas"]:
        nueva = _fila_catalogo(r)
        pasos = [tuple(p) for p in r["pasos"]]
        fila = con.execute(f"SELECT {','.join(CAMPOS_REGLA)} FROM reglas WHERE id_regla=?", (r["id_regla"],)).fetchone()
        if fila is None:
            con.execute(f"INSERT INTO reglas (id_regla,{','.join(CAMPOS_REGLA)}) VALUES (?,{','.join('?'*7)})",
                        [r["id_regla"]] + [nueva[c] for c in CAMPOS_REGLA])
            informe.append(f"{r['id_regla']}: alta versión {nueva['version']}")
        else:
            actual = dict(zip(CAMPOS_REGLA, fila))
            pasos_bd = [tuple(x) for x in con.execute(
                "SELECT orden_paso,id_tipo_evento,condicion FROM regla_pasos WHERE id_regla=? ORDER BY orden_paso",
                (r["id_regla"],))]
            igual = actual == nueva and pasos_bd == pasos
            if igual:
                informe.append(f"{r['id_regla']}: sin cambios")
                continue
            if nueva["version"] <= actual["version"]:
                raise CambioSinVersion(f"{r['id_regla']}: el catálogo difiere de la base pero la versión "
                                       f"{nueva['version']} no supera la {actual['version']}. Suba la versión.")
            con.execute(f"UPDATE reglas SET {','.join(c+'=?' for c in CAMPOS_REGLA)} WHERE id_regla=?",
                        [nueva[c] for c in CAMPOS_REGLA] + [r["id_regla"]])
            informe.append(f"{r['id_regla']}: versión {actual['version']} a {nueva['version']}")
        con.execute("DELETE FROM regla_pasos WHERE id_regla=?", (r["id_regla"],))
        con.executemany("INSERT INTO regla_pasos (id_regla,orden_paso,id_tipo_evento,condicion) VALUES (?,?,?,?)",
                        [(r["id_regla"], *p) for p in pasos])
    return informe


def escribir_alertas(con, senales):
    existentes = {(a, h) for a, h in con.execute("SELECT id_regla, huella_secuencia FROM alertas")}
    ultimo = con.execute("SELECT COALESCE(MAX(CAST(SUBSTR(id_alerta,4) AS INTEGER)),0) FROM alertas").fetchone()[0]
    nuevas = []
    for s in senales:
        if (s["id_regla"], s["huella_secuencia"]) in existentes:
            continue
        ultimo += 1
        id_alerta = f"AL-{ultimo:04d}"
        con.execute("""INSERT INTO alertas (id_alerta,id_regla,version_regla,id_usuario,id_cuenta,nivel_riesgo,
                       fecha_hora,estado,explicacion,huella_secuencia) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (id_alerta, s["id_regla"], s["version_regla"], s["id_usuario"], s["id_cuenta"],
                     s["nivel_riesgo"], str(s["fecha_hora"]), "ABIERTA", s["explicacion"], s["huella_secuencia"]))
        con.executemany("INSERT INTO alerta_eventos (id_alerta,id_evento,orden_secuencia,rol_evento) VALUES (?,?,?,?)",
                        [(id_alerta, e, o, r) for e, o, r in s["eventos"]])
        nuevas.append((id_alerta, s["id_regla"], s["id_usuario"]))
    return nuevas


def cargar_eventos(con):
    t = {n: pd.read_sql(f"SELECT * FROM {n}", con) for n in
         ["eventos", "accesos", "eventos_seguridad", "operaciones", "usuarios", "cuentas", "recursos_modulos"]}
    return m._unificar(t["eventos"], t["accesos"], t["eventos_seguridad"], t["operaciones"], t["usuarios"]), \
        t["cuentas"], t["recursos_modulos"]


def integrar(ruta_db, simular=False, catalogo=None):
    catalogo = catalogo or m.cargar_catalogo()
    con = sqlite3.connect(ruta_db)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN")
        informe = sincronizar_reglas(con, catalogo)
        ev, cu, rc = cargar_eventos(con)
        nuevas = escribir_alertas(con, m.ejecutar(ev, cu, rc, catalogo))
        if simular:
            con.rollback()
        else:
            con.commit()
        return informe, nuevas
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    ruta = sys.argv[1]
    inf, nue = integrar(ruta, simular="--simular" in sys.argv)
    print("\n".join(inf))
    print(f"Alertas nuevas: {len(nue)}" + (" (simulación, sin cambios)" if "--simular" in sys.argv else ""))
    for a in nue:
        print(" ", *a)
