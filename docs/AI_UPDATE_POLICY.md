# Política de IA y actualización técnica

Última verificación: 2026-08-15. Alcance exclusivo: `ventasbot-platform`.

## Base vigente

La documentación oficial de OpenAI recomienda actualmente la familia GPT-5.6: Sol para máxima capacidad, Terra para equilibrio entre capacidad y costo, y Luna para volumen sensible al costo. Las integraciones nuevas deben usar Responses API y mantener el modelo configurable; VentasBot no fija un modelo dentro de las reglas de negocio.

## Reglas para VentasBot

- La IA puede clasificar intención, recuperar conocimiento y proponer respuestas.
- Pedidos, stock, pagos, facturación y transiciones de entrega permanecen deterministas.
- Las acciones sensibles requieren límites por empresa, salida estructurada, trazas y aprobación humana.
- Un cambio de modelo sólo se promueve si supera evaluaciones representativas de conversaciones paraguayas en calidad, costo y latencia.
- Nunca se envían secretos, números completos de tarjeta ni información innecesaria del cliente al proveedor de IA.

## Actualización continua

- Dependabot revisa semanalmente dependencias Python y GitHub Actions.
- CI se ejecuta en cada push, pull request y también semanalmente.
- Antes de aceptar actualizaciones se ejecutan compilación, contratos, pruebas y validación JavaScript.
- Las APIs, modelos y precios se vuelven a comprobar en documentación oficial antes de implementar una integración.

Referencias oficiales:

- https://developers.openai.com/api/docs/models
- https://developers.openai.com/api/docs/guides/latest-model
