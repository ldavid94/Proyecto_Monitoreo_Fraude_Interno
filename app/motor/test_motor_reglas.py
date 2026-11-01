import pandas as pd
import pytest
import motor_reglas as m

XLSX = "Dataset_Base_Fraude_Interno.xlsx"
RECURSOS = pd.DataFrame({"id_recurso": ["MOD-LIMITES", "MOD-CAJA", "MOD-BD-ADMIN", "MOD-PERMISOS", "MOD-CUENTAS"], "nivel_sensibilidad": [3, 2, 5, 4, 1]})
CUENTAS = pd.DataFrame({"id_cuenta": ["CT-1"], "umbral_operacion": [50000.0]})
COLS = ["id_evento", "id_tipo_evento", "id_usuario", "fecha_hora", "id_cuenta", "monto", "id_tipo_operacion",
        "id_recurso", "resultado", "id_usuario_autorizador", "valor_anterior", "valor_nuevo", "rol_id"]


def frame(filas):
    filas = [f + [None] * (len(COLS) - len(f)) for f in filas]
    df = pd.DataFrame(filas, columns=COLS)
    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"])
    return df.sort_values("fecha_hora").reset_index(drop=True)


def ids(senales, regla):
    return [s for s in senales if s["id_regla"] == regla]


@pytest.fixture(scope="module")
def dataset():
    return m.cargar_xlsx(XLSX)


@pytest.fixture(scope="module")
def senales(dataset):
    return m.ejecutar(*dataset)


# ---------- escenarios con verdad de referencia (el motor no los lee; solo la prueba)
def test_escenarios_contra_verdad(senales):
    h = pd.read_excel(XLSX, sheet_name=["escenarios1", "escenario_eventos"])
    esc, ee = h["escenarios1"], h["escenario_eventos"]
    col_id = esc.columns[0]
    col_esp = [c for c in esc.columns if "esperado" in c.lower()][0]
    con_senal = {e for s in senales for e, _, r in s["eventos"] if r == "CLAVE"}
    fallos = []
    for r in esc.itertuples(index=False):
        d = r._asdict() if hasattr(r, "_asdict") else dict(zip(esc.columns, r))
        evs = set(ee[ee.id_escenario == r[0]].id_evento)
        genera = bool(evs & con_senal)
        esperado = r[list(esc.columns).index(col_esp)]
        if esperado == "ALERTA" and not genera:
            fallos.append((r[0], "debía alertar"))
        if esperado == "SIN_ALERTA" and genera:
            fallos.append((r[0], "no debía alertar"))
    assert not fallos, fallos


def test_esc011_alerta_y_esc012_silencio(senales):
    ev011 = {"EV-002246", "EV-002247", "EV-002248", "EV-002249", "EV-002250"}
    ev012 = {"EV-002344", "EV-002348", "EV-002360"}
    clave = lambda s: {e for e, _, r in s["eventos"] if r == "CLAVE"}
    assert any(clave(s) & ev011 for s in senales)
    assert not any(clave(s) & ev012 for s in senales)


def test_esc013_alerta_r11(senales):
    assert [s["id_usuario"] for s in ids(senales, "R-11")] == ["U-0422"]


def test_solo_cuatro_alertas_r07_como_dataset(senales):
    r07 = ids(senales, "R-07")
    assert [s["id_cuenta"] for s in r07] == ["CT-8841", "CT-8828", "CT-8835", "CT-8847"]


# ---------- bordes R-07
def r07(min_after_s, monto=60000, cuenta2="CT-1", orden="normal"):
    t1 = pd.Timestamp("2026-10-01 10:00:00")
    t2 = t1 + pd.Timedelta(seconds=min_after_s)
    if orden == "invertido":
        t1, t2 = t2, t1
    f = [["E1", "CAMBIO_LIMITE", "U-1", t1, "CT-1", None, None, None, None, None, None, None],
         ["E2", "OPERACION", "U-2", t2, cuenta2, monto, "RETIRO", None, None, None, None, None]]
    return m.ejecutar(frame(f), CUENTAS, RECURSOS)


@pytest.mark.parametrize("seg,esperado", [(29 * 60, 1), (30 * 60, 1), (30 * 60 + 1, 0), (31 * 60, 0), (45 * 60, 0)])
def test_r07_ventana(seg, esperado):
    assert len(ids(r07(seg), "R-07")) == esperado


def test_r07_umbral_estricto():
    assert len(ids(r07(600, monto=50000), "R-07")) == 0
    assert len(ids(r07(600, monto=50000.01), "R-07")) == 1


def test_r07_otra_cuenta_y_orden_invertido():
    assert not ids(r07(600, cuenta2="CT-2"), "R-07")
    assert not ids(r07(600, orden="invertido"), "R-07")


def test_r07_usuarios_distintos_alertan():
    s = ids(r07(600), "R-07")
    assert s and s[0]["id_usuario"] == "U-2"


# ---------- R-05 · T-02
def _fila(i, tipo, u, ts, **k):
    d = dict(id_evento=i, id_tipo_evento=tipo, id_usuario=u, fecha_hora=ts, id_cuenta=None, monto=None,
             id_tipo_operacion=None, id_recurso=None, resultado=None, id_usuario_autorizador=None,
             valor_anterior=None, valor_nuevo=None, rol_id="CAJERO")
    d.update(k)
    return [d[c] for c in COLS]


def test_r05_bordes_y_condiciones():
    t1 = pd.Timestamp("2026-10-01 10:00:00")
    def corre(min_, aut=None, ts=t1, resultado="EXITOSO", rec="MOD-LIMITES"):
        f = [_fila("P1", "ELEVACION_PRIVILEGIO", "U-1", ts, id_usuario_autorizador=aut, valor_anterior="1", valor_nuevo="2"),
             _fila("A1", "ACCESO_MODULO", "U-1", ts + pd.Timedelta(minutes=min_), id_recurso=rec, resultado=resultado)]
        return ids(m.ejecutar(frame(f), CUENTAS, RECURSOS), "R-05")
    assert len(corre(60)) == 1 and corre(61) == []
    assert corre(10, aut="U-9") == []                      # autorizada y en horario: legítima
    assert len(corre(10, aut="U-1")) == 1                  # autoautorizada
    assert len(corre(10, aut="U-9", ts=pd.Timestamp("2026-10-01 20:00:00"))) == 1   # fuera de horario
    assert corre(10, resultado="DENEGADO") == [] and corre(10, rec="MOD-CAJA") == []


def test_r05_factores_agravantes():
    t1 = pd.Timestamp("2026-10-01 10:00:00")
    f = [_fila("P1", "ELEVACION_PRIVILEGIO", "U-1", t1, valor_anterior="1", valor_nuevo="4"),
         _fila("A1", "ACCESO_MODULO", "U-1", t1 + pd.Timedelta(minutes=5), id_recurso="MOD-LIMITES", resultado="EXITOSO"),
         _fila("R1", "REVERSION_PRIVILEGIO", "U-1", t1 + pd.Timedelta(minutes=10))]
    s = ids(m.ejecutar(frame(f), CUENTAS, RECURSOS), "R-05")[0]
    assert any("salto de nivel" in x for x in s["factores"]) and any("reversión" in x for x in s["factores"])
    assert s["nivel_riesgo"] == "ALTO"


# ---------- R-03 · T-03
def test_r03_sin_reversion_antes_del_cierre():
    t1 = pd.Timestamp("2026-10-01 10:00:00")
    def corre(rev_ts=None, ts=t1):
        f = [_fila("P1", "ELEVACION_PRIVILEGIO", "U-1", ts, valor_anterior="1", valor_nuevo="2")]
        if rev_ts:
            f.append(_fila("R1", "REVERSION_PRIVILEGIO", "U-1", pd.Timestamp(rev_ts)))
        return ids(m.ejecutar(frame(f), CUENTAS, RECURSOS), "R-03")
    assert len(corre()) == 1
    assert corre("2026-10-01 18:59:59") == []              # revertida antes del cierre
    assert len(corre("2026-10-01 19:00:01")) == 1          # revertida tarde
    assert corre(ts=pd.Timestamp("2026-10-01 20:00:00")) == []   # fuera de horario: es caso de R-05
    assert corre()[0]["nivel_riesgo"] == "MEDIO"


# ---------- R-09 · T-04
def test_r09_accesos_a_modulos_ajenos():
    t = pd.Timestamp("2026-10-01 10:00:00")
    def corre(mods, gap=5, rol="CAJERO", elevar=False):
        f = [_fila(f"A{i}", "ACCESO_MODULO", "U-1", t + pd.Timedelta(minutes=gap * i), id_recurso=mod,
                   resultado="EXITOSO", rol_id=rol) for i, mod in enumerate(mods)]
        if elevar:
            f.append(_fila("P1", "ELEVACION_PRIVILEGIO", "U-1", t - pd.Timedelta(minutes=1), id_usuario_autorizador="U-9", rol_id=rol))
        return ids(m.ejecutar(frame(f), CUENTAS, RECURSOS), "R-09")
    assert len(corre(["MOD-BD-ADMIN", "MOD-PERMISOS"])) == 1
    assert corre(["MOD-BD-ADMIN"]) == []                    # un solo acceso ajeno no alcanza
    assert corre(["MOD-CAJA", "MOD-CUENTAS"]) == []         # módulos habituales del rol
    assert corre(["MOD-BD-ADMIN", "MOD-PERMISOS"], gap=31) == []   # fuera de ventana
    assert corre(["MOD-BD-ADMIN", "MOD-PERMISOS"], elevar=True) == []   # elevación vigente
    assert corre(["MOD-BD-ADMIN", "MOD-PERMISOS"])[0]["nivel_riesgo"] == "MEDIO"


def test_r11_requiere_denegado_y_minimo_de_fallos():
    t = pd.Timestamp("2026-10-01 09:00:00")
    def corre(n, denegado=True):
        f = [[f"F{i}", "LOGIN_FALLIDO", "U-1", t + pd.Timedelta(minutes=i), None, None, None, "MOD-AUTH", "FALLIDO", None, None, None] for i in range(n)]
        if denegado:
            f.append(["D", "ACCESO_MODULO", "U-1", t + pd.Timedelta(minutes=n + 2), None, None, None, "MOD-LIMITES", "DENEGADO", None, None, None])
        return ids(m.ejecutar(frame(f), CUENTAS, RECURSOS), "R-11")
    assert len(corre(3)) == 1 and corre(2) == [] and corre(3, denegado=False) == []


# ---------- explicabilidad, lenguaje y reproducibilidad
def test_lenguaje_sin_juicio_y_aviso(senales):
    prohibidos = ["fraude", "sospechoso", "culpable", "infractor", "implicado", "fraudulento", "mala fe"]
    for s in senales:
        t = s["explicacion"].lower()
        assert not any(p in t for p in prohibidos), s["id_alerta"]
        assert "no constituye conclusión" in t


def test_criticidad_fija_por_regla(senales):
    cat = {r["id_regla"]: r["criticidad"] for r in m.cargar_catalogo()["reglas"]}
    assert all(s["nivel_riesgo"] == cat[s["id_regla"]] for s in senales)


def test_reproducible(dataset):
    a = m.a_dataframe(m.ejecutar(*dataset))
    b = m.a_dataframe(m.ejecutar(*dataset))
    assert a.equals(b)


def test_cada_senal_tiene_eventos_clave_ordenados(senales, dataset):
    ev = dataset[0].set_index("id_evento")["fecha_hora"]
    for s in senales:
        assert sum(r == "CLAVE" for _, _, r in s["eventos"]) >= 1
        ts = [ev[e] for e, _, _ in s["eventos"]]
        assert ts == sorted(ts)


def test_r07_reproduce_alertas_del_dataset(senales):
    al = pd.read_excel(XLSX, sheet_name="alertas")
    ae = pd.read_excel(XLSX, sheet_name="alerta_eventos")
    r07 = ids(senales, "R-07")
    assert len(r07) == len(al)
    for s in r07:
        ref = al[al.huella_secuencia == s["huella_secuencia"]]
        assert len(ref) == 1 and ref.id_usuario.iloc[0] == s["id_usuario"]
        esperado = [(r.id_evento, r.orden_secuencia, r.rol_evento)
                    for r in ae[ae.id_alerta == ref.id_alerta.iloc[0]].sort_values("orden_secuencia").itertuples()]
        assert s["eventos"] == esperado
