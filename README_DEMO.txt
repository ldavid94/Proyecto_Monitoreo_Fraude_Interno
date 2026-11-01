DEMO V2 - MODELO DE MONITOREO DE FRAUDE INTERNO
================================================

INSTALACION (una sola vez)
1. Descomprima toda la carpeta en C:\FraudeInternoMVP (recomendado).
2. Verifique que Python 3.11 o superior este instalado.
3. Ejecute INSTALAR_DEMO.bat.
4. Al finalizar, ejecute INICIAR_DEMO.bat.
5. Abra http://127.0.0.1:8000 si no se abre automaticamente.

ARCHIVOS IMPORTANTES
- app\fraude_demo.db: base SQLite sintética.
- app\motor\reglas_catalogo.json: catálogo de 5 reglas.
- app\motor\motor_reglas.py: motor de correlación.
- app\motor\integrar_alertas.py: integración de señales con la BD.
- EJECUTAR_PRUEBAS.bat: ejecuta las 28 pruebas automatizadas.
- REINTEGRAR_REGLAS.bat: vuelve a sincronizar reglas y señales; es idempotente.

DEMO RECOMENDADA
1. Dashboard: mostrar las 6 alertas integradas.
2. Abrir AL-0005 (R-05) y mostrar elevación no autorizada + acceso sensible.
3. Abrir AL-0006 (R-11) y mostrar fallos de autenticación + acceso denegado.
4. Mostrar R-07 y el falso positivo esperado ESC-010.
5. Cambiar a rol Analista y abrir una investigación o pedir contexto.
6. Cambiar a Auditor/Privacidad y mostrar la bitácora.
7. Ejecutar EJECUTAR_PRUEBAS.bat para mostrar 28 pruebas superadas.

IMPORTANTE
- Todos los datos son sintéticos y seudonimizados.
- Una alerta es una señal para revisión humana; no es una conclusión.
- R-03 y R-09 están implementadas y probadas, pero el dataset base todavía no incluye escenarios específicos que las activen.
