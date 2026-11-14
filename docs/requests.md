# Peticiones entre roles

## Rol 1 (`src/data/ui_bridge.py`) — extrusión por defecto

**De:** Rol 2 (fradiaz)  
**Para:** Rol 1 (pozo)  
**Contexto:** cockpit mapa H3, rama `feat/ui-map-render`.

En `get_map_layer_df`, el default actual es:

```python
extrude = bool((filters or {}).get("extrude_3d", True))
```

El toggle de la UI arranca en `False`. Pedimos cambiar el default a `False` para que, si el dict de filtros omite la clave, el payload no llegue con `elevation_m` alto (hasta ~3000 m en el mock).

La UI ya aplana `height` en modo flat como red de seguridad; alinear el default evita trabajo duplicado y sorpresas si otro consumidor llama el hook sin `extrude_3d`.

**Impacto en contrato de datos:** ninguno (solo default de presentación del bridge).  
**Aprobación:** Rol 1; Rol 2 verifica el mapa tras el cambio.  
**Estado:** RESUELTO por Rol 1 (`pozo`) — default cambiado a `False` en `src/data/ui_bridge.py` y cubierto por test en `tests/test_ui_bridge.py`.
