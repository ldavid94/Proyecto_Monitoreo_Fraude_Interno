from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import sqlite3, os, hashlib, datetime

BASE = os.path.dirname(__file__)
DB = os.path.join(BASE, 'fraude_demo.db')
app = FastAPI(title='Demo - Monitoreo de Fraude Interno')
app.mount('/static', StaticFiles(directory=os.path.join(BASE,'static')), name='static')
templates = Jinja2Templates(directory=os.path.join(BASE,'templates'))

ROLES = {
    'ANALISTA_FRAUDE': 'Analista de fraude',
    'LIDER_CASOS': 'Líder de casos',
    'AUDITOR_PRIVACIDAD': 'Auditor / Privacidad',
    'ING_SEGURIDAD': 'Ingeniero de seguridad',
    'ADMIN_ACADEMICO': 'Administrador académico',
}
DEFAULT_USERS = {
    'ANALISTA_FRAUDE':'S-01', 'LIDER_CASOS':'S-03', 'AUDITOR_PRIVACIDAD':'S-04',
    'ING_SEGURIDAD':'S-05', 'ADMIN_ACADEMICO':'S-06'
}

def conn():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def rows(sql, params=()):
    with conn() as c:
        return [dict(x) for x in c.execute(sql, params).fetchall()]

def row(sql, params=()):
    with conn() as c:
        r=c.execute(sql, params).fetchone()
        return dict(r) if r else None

def role(request):
    r=request.query_params.get('rol') or request.cookies.get('demo_role') or 'ANALISTA_FRAUDE'
    return r if r in ROLES else 'ANALISTA_FRAUDE'

def has_permission(role_id, permission):
    with conn() as c:
        x=c.execute('SELECT 1 FROM rol_sistema_permiso WHERE id_rol_sistema=? AND id_permiso=?',(role_id,permission)).fetchone()
        return bool(x)

def audit(role_id, action, obj_type='SISTEMA', obj_id=None, result='PERMITIDO', reason=None):
    uid=DEFAULT_USERS.get(role_id,'S-01')
    with conn() as c:
        prev=c.execute('SELECT hash_registro FROM bitacora ORDER BY id_bitacora DESC LIMIT 1').fetchone()
        prev_hash=prev[0] if prev else '0'*64
        now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        payload='|'.join(str(x or '') for x in [prev_hash,uid,role_id,action,obj_type,obj_id,result,reason,now])
        h=hashlib.sha256(payload.encode()).hexdigest()
        c.execute('INSERT INTO bitacora(id_usuario_sistema,id_rol_sistema,accion,tipo_objeto,id_objeto,resultado,motivo,fecha_hora,hash_previo,hash_registro) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (uid,role_id,action,obj_type,obj_id,result,reason,now,prev_hash,h))
        c.commit()

def base_ctx(request, active):
    r=role(request)
    return {'request':request,'active':active,'role_id':r,'role_name':ROLES[r],'roles':ROLES}

@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    r=role(request)
    if not has_permission(r,'VER_DASHBOARD'):
        audit(r,'CONSULTA_DASHBOARD',result='DENEGADO')
        return templates.TemplateResponse('denied.html',{**base_ctx(request,'dashboard'),'permission':'VER_DASHBOARD'})
    alerts=rows('SELECT * FROM v_alertas_dashboard ORDER BY orden_riesgo DESC, fecha_hora DESC')
    stats=row("SELECT COUNT(*) total, SUM(CASE WHEN nivel_riesgo='ALTO' THEN 1 ELSE 0 END) altos, SUM(CASE WHEN estado='EN_INVESTIGACION' THEN 1 ELSE 0 END) investigacion, SUM(CASE WHEN estado='DESCARTADA' THEN 1 ELSE 0 END) descartadas FROM alertas")
    cm=row("SELECT SUM(CASE WHEN clasificacion='VERDADERO_POSITIVO' THEN 1 ELSE 0 END) vp, SUM(CASE WHEN clasificacion='VERDADERO_NEGATIVO' THEN 1 ELSE 0 END) vn, SUM(CASE WHEN clasificacion='FALSO_POSITIVO' THEN 1 ELSE 0 END) fp, SUM(CASE WHEN clasificacion='FALSO_NEGATIVO' THEN 1 ELSE 0 END) fn FROM v_resultado_escenarios WHERE id_regla_objetivo='R-07'")
    audit(r,'CONSULTA_DASHBOARD')
    return templates.TemplateResponse('dashboard.html',{**base_ctx(request,'dashboard'),'alerts':alerts,'stats':stats,'cm':cm})

@app.get('/alertas/{alert_id}', response_class=HTMLResponse)
def alert_detail(request: Request, alert_id:str):
    r=role(request)
    if not has_permission(r,'VER_DETALLE_ALERTA'):
        audit(r,'CONSULTA_DETALLE_ALERTA','ALERTA',alert_id,'DENEGADO')
        return templates.TemplateResponse('denied.html',{**base_ctx(request,'dashboard'),'permission':'VER_DETALLE_ALERTA'})
    a=row('SELECT a.*, r.nombre nombre_regla, r.descripcion regla_descripcion, r.ventana_minutos FROM alertas a JOIN reglas r ON r.id_regla=a.id_regla WHERE a.id_alerta=?',(alert_id,))
    if not a: raise HTTPException(404)
    events=rows('SELECT * FROM v_detalle_alerta WHERE id_alerta=? ORDER BY orden_secuencia',(alert_id,))
    reviews=rows('SELECT * FROM revisiones_alerta WHERE id_alerta=? ORDER BY fecha_hora DESC',(alert_id,))
    investigation=row('SELECT * FROM investigaciones WHERE id_alerta=? ORDER BY fecha_apertura DESC LIMIT 1',(alert_id,))
    audit(r,'CONSULTA_DETALLE_ALERTA','ALERTA',alert_id)
    perms={p:has_permission(r,p) for p in ['ABRIR_INVESTIGACION','DESCARTAR_ALERTA','SOLICITAR_CONTEXTO']}
    return templates.TemplateResponse('alert_detail.html',{**base_ctx(request,'dashboard'),'a':a,'events':events,'reviews':reviews,'investigation':investigation,'perms':perms})

@app.post('/alertas/{alert_id}/accion')
def alert_action(request:Request, alert_id:str, accion:str=Form(...), motivo:str=Form(...)):
    r=role(request); perm={'ABRIR':'ABRIR_INVESTIGACION','DESCARTAR':'DESCARTAR_ALERTA','CONTEXTO':'SOLICITAR_CONTEXTO'}.get(accion)
    if not perm or not has_permission(r,perm):
        audit(r,accion,'ALERTA',alert_id,'DENEGADO',motivo)
        return RedirectResponse(f'/alertas/{alert_id}?rol={r}',303)
    uid=DEFAULT_USERS[r]; now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with conn() as c:
        nrev=c.execute("SELECT COALESCE(MAX(CAST(substr(id_revision,5) AS INTEGER)),0)+1 FROM revisiones_alerta").fetchone()[0]
        rev=f'REV-{nrev:04d}'
        inv_id=None
        if accion=='ABRIR':
            ninv=c.execute("SELECT COALESCE(MAX(CAST(substr(id_investigacion,5) AS INTEGER)),0)+1 FROM investigaciones").fetchone()[0]
            inv_id=f'INV-{ninv:04d}'
            c.execute('INSERT INTO investigaciones(id_investigacion,id_alerta,estado,id_responsable,motivo_apertura,fecha_apertura) VALUES(?,?,?,?,?,?)',(inv_id,alert_id,'ABIERTA',uid,motivo,now))
            c.execute("UPDATE alertas SET estado='EN_INVESTIGACION' WHERE id_alerta=?",(alert_id,))
            rev_action='ABRIR_INVESTIGACION'
        elif accion=='DESCARTAR':
            c.execute("UPDATE alertas SET estado='DESCARTADA' WHERE id_alerta=?",(alert_id,)); rev_action='DESCARTAR'
        else:
            c.execute("UPDATE alertas SET estado='PENDIENTE_CONTEXTO' WHERE id_alerta=?",(alert_id,)); rev_action='SOLICITAR_CONTEXTO'
        c.execute('INSERT INTO revisiones_alerta(id_revision,id_alerta,id_investigacion,accion,id_usuario_sistema,motivo,fecha_hora) VALUES(?,?,?,?,?,?,?)',(rev,alert_id,inv_id,rev_action,uid,motivo,now))
        c.commit()
    audit(r,perm,'ALERTA',alert_id,'PERMITIDO',motivo)
    return RedirectResponse(f'/alertas/{alert_id}?rol={r}',303)

@app.get('/escenarios', response_class=HTMLResponse)
def scenarios(request:Request):
    r=role(request); data=rows('SELECT * FROM v_resultado_escenarios ORDER BY id_escenario')
    return templates.TemplateResponse('scenarios.html',{**base_ctx(request,'escenarios'),'scenarios':data})

@app.get('/reglas', response_class=HTMLResponse)
def rules_page(request:Request):
    r=role(request)
    data=rows('SELECT * FROM reglas ORDER BY id_regla')
    steps=rows('SELECT * FROM regla_pasos ORDER BY id_regla,orden_paso')
    return templates.TemplateResponse('rules.html',{**base_ctx(request,'reglas'),'rules':data,'steps':steps})

@app.get('/bitacora', response_class=HTMLResponse)
def log_page(request:Request):
    r=role(request)
    if not has_permission(r,'VER_BITACORA'):
        audit(r,'CONSULTA_BITACORA','BITACORA',None,'DENEGADO')
        return templates.TemplateResponse('denied.html',{**base_ctx(request,'bitacora'),'permission':'VER_BITACORA'})
    data=rows('SELECT * FROM bitacora ORDER BY id_bitacora DESC LIMIT 100')
    audit(r,'CONSULTA_BITACORA','BITACORA')
    return templates.TemplateResponse('log.html',{**base_ctx(request,'bitacora'),'logs':data})

@app.get('/api/health')
def health(): return {'status':'ok','dataset':'sintetico','db':'sqlite'}
@app.get('/api/alertas')
def api_alerts(): return JSONResponse(rows('SELECT * FROM v_alertas_dashboard ORDER BY orden_riesgo DESC'))
@app.get('/api/escenarios')
def api_scenarios(): return JSONResponse(rows('SELECT * FROM v_resultado_escenarios ORDER BY id_escenario'))
