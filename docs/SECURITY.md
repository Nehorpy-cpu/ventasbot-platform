# Seguridad y privacidad

## Límites y controles

- Meta/Bancard son externos: webhooks autenticados, idempotentes y auditados.
- Toda consulta empresarial filtra por el `tenant_id` del usuario autenticado.
- Tracking usa token aleatorio y expone solo información operativa.
- Claves se referencian por variable; no se guardan en SQL ni logs.
- JWT expira, contraseñas Argon2, roles y empresa activa.
- HMAC Meta usa bytes crudos; pagos y mensajes se deduplican.
- Stock se reserva al confirmar; tarjeta nunca atraviesa el backend.

## Obligatorio antes del piloto

- PostgreSQL, migraciones, backups y restauración ensayada.
- Rate limiting, cabeceras, CORS explícito, HTTPS y secret manager.
- Cola persistente; `BackgroundTasks` no sobrevive un reinicio.
- Validar el callback Bancard y consultar su estado server-to-server.
- Minimizar RUC, comprobantes y ubicación antes de usar IA.
- Retención, exportación y borrado por empresa.
- SAST/dependencias y pruebas de aislamiento en cada release.

## Riesgos conocidos

- “Sin salir de WhatsApp” es el navegador integrado hacia Bancard; cobrar tarjeta por texto es inseguro.
- Una URL de tracking filtrada funciona hasta expirar; agregar expiración/rotación.
- Contacto/ubicación al repartidor deben quedar limitados al pedido asignado y auditados.
