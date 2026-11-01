"""Motor de correlación determinista para el prototipo de monitoreo de fraude interno.

Una regla por tipología de fraude interno. Lee eventos de la vista unificada, evalúa las
reglas del catálogo JSON y genera señales con explicación y factores agravantes.
No lee las tablas de escenarios, que son la verdad de referencia de las pruebas.
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

RUTA_CATALOGO = Path(__file__).with_name("reglas_catalogo.json")


# ---------------------------------------------------------------- carga
def _unificar(ev, ac, se, op, us):
    base = ev.copy()
    base["fecha_hora"] = pd.to_datetime(base["fecha_hora"])
    base = base.merge(ac.drop(columns="tipo_fuente"), on="id_evento", how="left")
    base = base.merge(se.drop(columns="tipo_fuente"), on="id_evento", how="left")
    base = base.merge(op.drop(columns="tipo_fuente"), on="id_evento", how="left", suffixes=("", "_op"))
    base["id_cuenta"] = base["id_cuenta"].fillna(base.get("id_cuenta_op"))
    base = base.drop(columns=[c for c in base.columns if c.endswith("_op")])
    base = base.merge(us[["id_usuario", "rol_id"]], on="id_usuario", how="left")
    return base.sort_values(["fecha_hora", "id_evento"]).reset_index(drop=True)


TABLAS = ["eventos", "accesos", "eventos_seguridad", "operaciones", "usuarios", "cuentas", "recursos_modulos"]


def _desde(t):
    return _unificar(t["eventos"], t["accesos"], t["eventos_seguridad"], t["operaciones"], t["usuarios"]), \
        t["cuentas"], t["recursos_modulos"]


def cargar_xlsx(ruta):
    return _desde(pd.read_excel(ruta, sheet_name=TABLAS))


def cargar_sqlite(ruta):
    con = sqlite3.connect(ruta)
    t = {n: pd.read_sql(f"SELECT * FROM {n}", con) for n in TABLAS}
    con.close()
    return _desde(t)


def cargar_catalogo(ruta=RUTA_CATALOGO):
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- utilidades
def _huella(id_regla, ids):
    return hashlib.sha256((id_regla + "|" + "|".join(ids)).encode()).hexdigest()


def _mmss(seg):
    return int(seg // 60), int(seg % 60)


def _q(x):
    return f"{x:,.2f}"


def _hora(s):
    return pd.to_datetime(s).time()


def _en_horario(t, p):
    return t.weekday() in p["dias_habiles"] and _hora(p["horario_inicio"]) <= t.time() < _hora(p["horario_fin"])


def _factores(lista):
    return ("Factores a considerar: " + "; ".join(lista) + ".") if lista else "No se identifican factores adicionales."


def _senal(regla, id_usuario, id_cuenta, fecha, explicacion, eventos, factores=()):
    """eventos: lista de (id_evento, rol_evento) en orden cronológico."""
    claves = [e for e, r in eventos if r == "CLAVE"]
    return {
        "id_regla": regla["id_regla"], "tipologia": regla.get("tipologia"), "version_regla": regla["version"],
        "id_usuario": id_usuario, "id_cuenta": id_cuenta,
        "nivel_riesgo": regla["criticidad"], "fecha_hora": fecha,
        "explicacion": explicacion, "factores": list(factores),
        "huella_secuencia": _huella(regla["id_regla"], claves),
        "eventos": [(e, i + 1, r) for i, (e, r) in enumerate(eventos)],
    }


def _ordenar(eventos, ev):
    t = ev.set_index("id_evento")["fecha_hora"]
    vistos, out = set(), []
    for e, r in sorted(eventos, key=lambda x: (t[x[0]], x[0])):
        if e not in vistos:
            vistos.add(e)
            out.append((e, r))
    return out


def _acceso_sensible(ev, rec, p):
    sens = rec.set_index("id_recurso")["nivel_sensibilidad"]
    return ev[(ev.id_tipo_evento == "ACCESO_MODULO") & (ev.resultado == p["resultado_acceso"])
              & (ev.id_recurso.map(sens) >= p["sensibilidad_minima"])]


# ---------------------------------------------------------------- R-07 · T-01
def evaluar_r07(regla, ev, cuentas, _rec):
    p = regla["parametros"]
    cambios = ev[ev.id_tipo_evento == p["tipo_evento_inicio"]][["id_evento", "id_cuenta", "fecha_hora", "id_usuario", "rol_id"]]
    ops = ev[ev.id_tipo_evento == p["tipo_evento_fin"]][["id_evento", "id_cuenta", "fecha_hora", "id_usuario",
                                                          "id_tipo_operacion", "monto"]]
    acc = ev[ev.id_tipo_evento == "ACCESO_MODULO"]
    par = cambios.merge(ops, on="id_cuenta", suffixes=("_1", "_2")).merge(
        cuentas[["id_cuenta", "umbral_operacion"]], on="id_cuenta")
    par["seg"] = (par.fecha_hora_2 - par.fecha_hora_1).dt.total_seconds()
    sobre = par.monto > par.umbral_operacion if p["umbral_estricto"] else par.monto >= par.umbral_operacion
    lim = p["ventana_minutos"] * 60
    en_ventana = (par.seg <= lim) if p["ventana_inclusiva"] else (par.seg < lim)
    orden = (par.seg > 0) if p["orden_estricto"] else (par.seg >= 0)
    sal = []
    for r in par[sobre & en_ventana & orden].sort_values(["fecha_hora_2", "fecha_hora_1"]).itertuples():
        mi, s = _mmss(r.seg)
        fac = []
        if r.id_usuario_1 == r.id_usuario_2:
            fac.append("la misma persona hace el cambio y la operación")
        if r.rol_id not in p["roles_gestionan_limites"]:
            fac.append("el cambio lo hace un rol que normalmente no gestiona límites")
        if r.monto / r.umbral_operacion >= p["ratio_monto_agravante"]:
            fac.append(f"monto {round((r.monto / r.umbral_operacion - 1) * 100)} % sobre el umbral")
        txt = regla["plantilla_explicacion"].format(
            id_regla=regla["id_regla"], id_cuenta=r.id_cuenta, id_evento_1=r.id_evento_1,
            t1=r.fecha_hora_1, tipo_operacion=r.id_tipo_operacion, monto=_q(r.monto),
            umbral=_q(r.umbral_operacion), id_evento_2=r.id_evento_2, t2=r.fecha_hora_2,
            minutos=mi, segundos=s, ventana=p["ventana_minutos"], factores=_factores(fac))
        eventos = [(r.id_evento_1, "CLAVE"), (r.id_evento_2, "CLAVE")]
        for u, t in ((r.id_usuario_1, r.fecha_hora_1), (r.id_usuario_2, r.fecha_hora_2)):
            ctx = acc[(acc.id_usuario == u) & (acc.fecha_hora < t)
                      & (acc.fecha_hora >= t - pd.Timedelta(minutes=p["contexto_minutos_previos"]))]
            eventos += [(c.id_evento, "CONTEXTO") for c in ctx.itertuples()]
        sal.append(_senal(regla, r.id_usuario_2, r.id_cuenta, r.fecha_hora_2, txt, _ordenar(eventos, ev), fac))
    return sal


# ---------------------------------------------------------------- R-05 · T-02
def evaluar_r05(regla, ev, _c, rec):
    p = regla["parametros"]
    acc = _acceso_sensible(ev, rec, p)
    lim = p["ventana_minutos"] * 60
    sal = []
    for e in ev[ev.id_tipo_evento == p["tipo_evento_inicio"]].itertuples():
        motivos = []
        if pd.isna(e.id_usuario_autorizador):
            motivos.append("sin usuario autorizador")
        elif e.id_usuario_autorizador == e.id_usuario:
            motivos.append("autorización del propio usuario")
        if not _en_horario(e.fecha_hora, p):
            motivos.append("hora fuera del horario laboral")
        if not motivos:
            continue
        sig = acc[(acc.id_usuario == e.id_usuario) & (acc.fecha_hora > e.fecha_hora)]
        for a in sig.itertuples():
            seg = (a.fecha_hora - e.fecha_hora).total_seconds()
            if not ((seg <= lim) if p["ventana_inclusiva"] else (seg < lim)):
                continue
            fac = []
            try:
                salto = int(float(e.valor_nuevo)) - int(float(e.valor_anterior))
            except (TypeError, ValueError):
                salto = 0
            if salto >= p["salto_nivel_agravante"]:
                fac.append(f"salto de nivel de {e.valor_anterior} a {e.valor_nuevo}")
            if a.id_recurso in p["modulos_administracion"]:
                fac.append(f"acceso a {a.id_recurso}, módulo de administración")
            rev = ev[(ev.id_tipo_evento == p["tipo_reversion"]) & (ev.id_usuario == e.id_usuario)
                     & (ev.fecha_hora > a.fecha_hora)
                     & (ev.fecha_hora <= a.fecha_hora + pd.Timedelta(minutes=p["minutos_reversion_agravante"]))]
            if not rev.empty:
                fac.append("reversión manual poco después del acceso")
            mi, s = _mmss(seg)
            txt = regla["plantilla_explicacion"].format(
                id_regla=regla["id_regla"], id_usuario=e.id_usuario, valor_anterior=e.valor_anterior,
                valor_nuevo=e.valor_nuevo, id_evento_1=e.id_evento, t1=e.fecha_hora,
                motivos=" y ".join(motivos), id_recurso=a.id_recurso, id_evento_2=a.id_evento,
                t2=a.fecha_hora, minutos=mi, segundos=s, ventana=p["ventana_minutos"], factores=_factores(fac))
            eventos = [(e.id_evento, "CLAVE"), (a.id_evento, "CLAVE")]
            eventos += [(r.id_evento, "CONTEXTO") for r in rev.itertuples()]
            ini = ev[(ev.id_usuario == e.id_usuario) & (ev.id_tipo_evento == "LOGIN") & (ev.fecha_hora < e.fecha_hora)
                     & (ev.fecha_hora >= e.fecha_hora - pd.Timedelta(hours=1))]
            eventos += [(r.id_evento, "CONTEXTO") for r in ini.itertuples()]
            sal.append(_senal(regla, e.id_usuario, None, a.fecha_hora, txt, _ordenar(eventos, ev), fac))
            break
    return sal


# ---------------------------------------------------------------- R-03 · T-03
def evaluar_r03(regla, ev, _c, rec):
    p = regla["parametros"]
    cierre_h = _hora(p["cierre_jornada"])
    acc = _acceso_sensible(ev, rec, {"resultado_acceso": "EXITOSO", "sensibilidad_minima": p["sensibilidad_minima"]})
    sal = []
    for e in ev[ev.id_tipo_evento == p["tipo_evento"]].itertuples():
        t = e.fecha_hora
        if not _en_horario(t, {"dias_habiles": p["dias_habiles"], "horario_inicio": p["horario_inicio"],
                               "horario_fin": p["cierre_jornada"]}):
            continue
        cierre = pd.Timestamp.combine(t.date(), cierre_h)
        rev = ev[(ev.id_tipo_evento == p["tipo_reversion"]) & (ev.id_usuario == e.id_usuario)
                 & (ev.fecha_hora > t) & (ev.fecha_hora <= cierre)]
        if not rev.empty:
            continue
        fac = []
        try:
            if int(float(e.valor_nuevo)) >= p["nivel_maximo"]:
                fac.append("el nivel resultante es el máximo")
        except (TypeError, ValueError):
            pass
        post = acc[(acc.id_usuario == e.id_usuario) & (acc.fecha_hora > cierre)
                   & (acc.fecha_hora <= cierre + pd.Timedelta(hours=p["horas_accesos_posteriores"]))]
        if not post.empty:
            fac.append("hay accesos a módulos sensibles después del cierre de la jornada")
        txt = regla["plantilla_explicacion"].format(
            id_regla=regla["id_regla"], id_usuario=e.id_usuario, valor_anterior=e.valor_anterior,
            valor_nuevo=e.valor_nuevo, fecha_hora=t, cierre=p["cierre_jornada"], factores=_factores(fac))
        ctx = ev[(ev.id_usuario == e.id_usuario) & (ev.fecha_hora > t) & (ev.fecha_hora <= cierre)
                 & (ev.id_tipo_evento == "ACCESO_MODULO")]
        eventos = [(e.id_evento, "CLAVE")] + [(c.id_evento, "CONTEXTO") for c in ctx.itertuples()] \
            + [(c.id_evento, "CONTEXTO") for c in post.itertuples()]
        sal.append(_senal(regla, e.id_usuario, None, cierre, txt, _ordenar(eventos, ev), fac))
    return sal


# ---------------------------------------------------------------- R-09 · T-04
def evaluar_r09(regla, ev, _c, rec):
    p = regla["parametros"]
    sens = rec.set_index("id_recurso")["nivel_sensibilidad"]
    habituales = p["modulos_habituales_por_rol"]
    acc = ev[(ev.id_tipo_evento == "ACCESO_MODULO") & (ev.resultado == p["resultado_acceso"])]
    ajeno = pd.Series([r not in habituales.get(rol, []) for r, rol in zip(acc.id_recurso, acc.rol_id)], index=acc.index, dtype=bool)
    acc = acc[ajeno]
    sal, usados = [], set()
    for u, g in acc.groupby("id_usuario"):
        g = g.sort_values("fecha_hora").reset_index(drop=True)
        for i in range(len(g)):
            fin = g.fecha_hora[i] + pd.Timedelta(minutes=p["ventana_minutos"])
            grupo = g[(g.fecha_hora >= g.fecha_hora[i]) & (g.fecha_hora <= fin)]
            # solo accesos sin elevación vigente
            vigente = []
            for a in grupo.itertuples():
                el = ev[(ev.id_usuario == u) & (ev.id_tipo_evento == p["tipo_elevacion"]) & (ev.fecha_hora < a.fecha_hora)]
                ok = False
                for x in el.itertuples():
                    rv = ev[(ev.id_usuario == u) & (ev.id_tipo_evento == p["tipo_reversion"]) & (ev.fecha_hora > x.fecha_hora)]
                    fin_el = rv.fecha_hora.min() if not rv.empty else pd.Timestamp.max
                    ok = ok or (x.fecha_hora < a.fecha_hora <= fin_el)
                if not ok:
                    vigente.append(a)
            if len(vigente) < p["min_accesos"]:
                continue
            ids = tuple(a.id_evento for a in vigente)
            if ids in usados or any(set(ids) <= set(x) for x in usados):
                continue
            usados.add(ids)
            mods = sorted({a.id_recurso for a in vigente})
            fac = []
            if any(sens.get(m_, 0) >= p["sensibilidad_minima"] or m_ in p["modulos_fondos"] for m_ in mods):
                fac.append("los módulos son de alta sensibilidad o permiten mover fondos")
            if any(not _en_horario(a.fecha_hora, p) for a in vigente):
                fac.append("hay accesos fuera de horario")
            txt = regla["plantilla_explicacion"].format(
                id_regla=regla["id_regla"], n_accesos=len(vigente), id_usuario=u, rol=vigente[0].rol_id,
                lista=", ".join(mods), t_primero=vigente[0].fecha_hora, t_ultimo=vigente[-1].fecha_hora,
                factores=_factores(fac))
            sal.append(_senal(regla, u, None, vigente[-1].fecha_hora, txt,
                              _ordenar([(i_, "CLAVE") for i_ in ids], ev), fac))
    return sal


# ---------------------------------------------------------------- R-11 · T-05
def evaluar_r11(regla, ev, _c, rec):
    p = regla["parametros"]
    sens = rec.set_index("id_recurso")["nivel_sensibilidad"]
    fallos = ev[ev.id_tipo_evento == p["tipo_evento_fallo"]]
    den = ev[(ev.resultado == p["resultado_acceso"]) & (ev.id_recurso.map(sens) >= p["sensibilidad_minima"])]
    base, vistos = [], set()
    for u, g in fallos.groupby("id_usuario"):
        g = g.sort_values("fecha_hora").reset_index(drop=True)
        for i in range(len(g)):
            fin_v = g.fecha_hora[i] + pd.Timedelta(minutes=p["ventana_fallos_minutos"])
            racha = g[(g.fecha_hora >= g.fecha_hora[i]) & (g.fecha_hora <= fin_v)]
            if len(racha) < p["min_fallos"]:
                continue
            ultimo = racha.fecha_hora.iloc[-1]
            sig = den[(den.id_usuario == u) & (den.fecha_hora > ultimo)
                      & (den.fecha_hora <= ultimo + pd.Timedelta(minutes=p["ventana_acceso_minutos"]))]
            if sig.empty:
                continue
            a = sig.iloc[0]
            if (u, a.id_evento) in vistos:
                continue
            vistos.add((u, a.id_evento))
            base.append((u, racha, ultimo, a))
    dias_por_usuario = {}
    for u, racha, ultimo, a in base:
        dias_por_usuario.setdefault(u, set()).add(ultimo.date())
    sal = []
    for u, racha, ultimo, a in base:
        fac = []
        if a.id_recurso in p["modulos_gestion"]:
            fac.append(f"el módulo es {a.id_recurso}, de gestión de límites o permisos")
        if len(dias_por_usuario[u]) >= p["dias_repeticion_agravante"]:
            fac.append("el patrón se repite en varios días")
        mi, s = _mmss((a.fecha_hora - ultimo).total_seconds())
        txt = regla["plantilla_explicacion"].format(
            id_regla=regla["id_regla"], n_fallos=len(racha), id_usuario=u,
            t_primero=racha.fecha_hora.iloc[0], t_ultimo=ultimo, id_recurso=a.id_recurso,
            id_evento_2=a.id_evento, t2=a.fecha_hora, minutos=mi, segundos=s, factores=_factores(fac))
        eventos = [(x, "CLAVE") for x in racha.id_evento] + [(a.id_evento, "CLAVE")]
        ctx = ev[(ev.id_usuario == u) & (ev.id_tipo_evento == "LOGIN") & (ev.fecha_hora > ultimo)
                 & (ev.fecha_hora < a.fecha_hora)]
        eventos += [(c.id_evento, "CONTEXTO") for c in ctx.itertuples()]
        sal.append(_senal(regla, u, None, a.fecha_hora, txt, _ordenar(eventos, ev), fac))
    return sal


EVALUADORES = {"secuencia_limite_operacion": evaluar_r07, "elevacion_no_autorizada_acceso": evaluar_r05,
               "privilegio_sin_reversion": evaluar_r03, "accesos_modulos_ajenos": evaluar_r09,
               "rafaga_fallos_acceso_denegado": evaluar_r11}


# ---------------------------------------------------------------- motor
def ejecutar(ev, cuentas, recursos, catalogo=None):
    catalogo = catalogo or cargar_catalogo()
    senales = []
    for regla in catalogo["reglas"]:
        if regla["activa"]:
            senales += EVALUADORES[regla["tipo"]](regla, ev, cuentas, recursos)
    senales.sort(key=lambda s: (s["fecha_hora"], s["id_regla"]))
    for i, s in enumerate(senales, 1):
        s["id_alerta"] = f"AL-{i:04d}"
    return senales


def a_dataframe(senales):
    return pd.DataFrame([{k: v for k, v in s.items() if k not in ("eventos", "factores")} | {
        "ids_eventos": ",".join(e for e, _, _ in s["eventos"])} for s in senales])


if __name__ == "__main__":
    ruta = sys.argv[1] if len(sys.argv) > 1 else "Dataset_Base_Fraude_Interno.xlsx"
    ev, cu, rc = (cargar_sqlite if ruta.endswith(".db") else cargar_xlsx)(ruta)
    df = a_dataframe(ejecutar(ev, cu, rc))
    print(df[["id_alerta", "id_regla", "tipologia", "id_usuario", "id_cuenta", "nivel_riesgo", "fecha_hora"]].to_string(index=False))
    df.to_csv("alertas_generadas.csv", index=False)
