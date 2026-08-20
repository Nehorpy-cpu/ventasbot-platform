// Panel de VentasBot. Sin framework: fetch + innerHTML, y todo lo que viene
// del servidor pasa por esc() antes de entrar al DOM.

const state = {
  token: sessionStorage.getItem('vb_token'),
  me: null,
  tenantId: null,
  view: 'dashboard',
  pedidoAbierto: null,
};

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>'"]/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[c]));
const money = v => new Intl.NumberFormat('es-PY', {
  style: 'currency', currency: 'PYG', maximumFractionDigits: 0,
}).format(v || 0);
const fecha = v => v ? new Date(v).toLocaleString('es-PY', { dateStyle: 'short', timeStyle: 'short' }) : '—';

function toast(mensaje, tipo = 'ok') {
  const el = $('#toast');
  el.textContent = mensaje;
  el.classList.toggle('error', tipo === 'error');
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2800);
}

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const r = await fetch(path, { ...options, headers });
  if (r.status === 401) { logout(); throw new Error('Sesión vencida'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(detalle(data));
  return data;
}

// FastAPI devuelve 422 con una lista de errores; mostrar "[object Object]"
// no le sirve a nadie.
function detalle(data) {
  const d = data.detail;
  if (!d) return 'No se pudo completar la operación';
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map(e => `${(e.loc || []).slice(-1)}: ${e.msg}`).join(' · ');
  return JSON.stringify(d);
}

function logout() {
  sessionStorage.removeItem('vb_token');
  state.token = null;
  state.me = null;
  $('#app-view').classList.add('hidden');
  $('#login-view').classList.remove('hidden');
}

async function boot() {
  if (!state.token) return;
  try {
    state.me = await api('/api/me');
    state.tenantId = state.me.tenant_id;
    $('#login-view').classList.add('hidden');
    $('#app-view').classList.remove('hidden');
    $('#user-name').textContent = state.me.name;
    $('#user-role').textContent = state.me.role;
    const esPlataforma = state.me.role === 'PLATFORM_ADMIN';
    $$('.platform-only').forEach(x => x.classList.toggle('hidden', !esPlataforma));
    $$('.tenant-only').forEach(x => x.classList.toggle('hidden', esPlataforma));
    $$('.owner-only').forEach(x => x.classList.toggle(
      'hidden', !['TENANT_OWNER', 'TENANT_MANAGER'].includes(state.me.role)));
    render('dashboard');
  } catch (e) {
    logout();
  }
}

$('#login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const datos = Object.fromEntries(new FormData(e.currentTarget));
  if (!datos.tenant_slug) delete datos.tenant_slug;
  $('#login-error').textContent = '';
  try {
    const data = await api('/api/auth/login', { method: 'POST', body: JSON.stringify(datos) });
    state.token = data.access_token;
    sessionStorage.setItem('vb_token', state.token);
    await boot();
  } catch (err) {
    $('#login-error').textContent = err.message;
  }
});

$('#logout').addEventListener('click', logout);
$$('nav button').forEach(b => b.addEventListener('click', () => render(b.dataset.view)));

const VISTAS = {
  dashboard: { titulo: 'Resumen', cargar: () => dashboard() },
  tenants: { titulo: 'Empresas', cargar: () => tenants() },
  products: { titulo: 'Catálogo', cargar: () => products() },
  orders: { titulo: 'Pedidos', cargar: () => orders() },
  whatsapp: { titulo: 'WhatsApp', cargar: () => whatsapp() },
};

async function render(view) {
  state.view = view;
  state.pedidoAbierto = null;
  $$('nav button').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  $('#page-title').textContent = VISTAS[view].titulo;
  $('#context-label').textContent = state.me.role === 'PLATFORM_ADMIN'
    ? 'PLATAFORMA' : `EMPRESA · ${state.me.role}`;
  $('#content').innerHTML = '<div class="empty">Cargando…</div>';
  try {
    await VISTAS[view].cargar();
  } catch (e) {
    $('#content').innerHTML = `<div class="card empty">${esc(e.message)}</div>`;
  }
}

const tarjeta = (rotulo, valor) =>
  `<article class="card metric"><small>${rotulo}</small><strong>${valor}</strong></article>`;

async function dashboard() {
  if (state.me.role === 'PLATFORM_ADMIN') {
    const s = await api('/api/platform/summary');
    $('#content').innerHTML = `<div class="grid">
      ${tarjeta('EMPRESAS', s.tenants)}${tarjeta('USUARIOS', s.users)}${tarjeta('PEDIDOS', s.orders)}
    </div>
    <article class="card section-card"><div class="section-head"><h2>Centro de control</h2>
      <span class="badge">Superadmin</span></div>
      <p class="muted">Creá empresas, supervisá la operación y habilitá integraciones desde un único lugar.</p>
    </article>`;
    return;
  }
  const [productos, pedidos, wa] = await Promise.all([
    api(`/api/tenants/${state.tenantId}/products`),
    api(`/api/tenants/${state.tenantId}/orders`),
    api(`/api/tenants/${state.tenantId}/whatsapp`).catch(() => null),
  ]);
  const facturado = pedidos.filter(x => x.status !== 'CANCELLED').reduce((a, x) => a + x.total, 0);
  const abiertos = pedidos.filter(x => !['DELIVERED', 'CANCELLED'].includes(x.status)).length;
  const aviso = wa
    ? `<p class="muted">Tu número <b>${esc(wa.display_phone_number || wa.phone_number_id)}</b> está
       ${wa.active ? 'activo' : '<b>desactivado</b>'}${wa.verificado_en ? ' y verificado' : ' (sin verificar todavía)'}.</p>`
    : `<p class="aviso">Todavía no cargaste tu número de WhatsApp: el bot no puede atender a tus clientes.
       Andá a <b>WhatsApp</b> en el menú para cargarlo.</p>`;
  $('#content').innerHTML = `<div class="grid">
    ${tarjeta('PRODUCTOS', productos.length)}${tarjeta('PEDIDOS ABIERTOS', abiertos)}${tarjeta('VALOR OPERADO', money(facturado))}
  </div>
  <article class="card section-card"><div class="section-head"><h2>Operación de hoy</h2>
    <span class="badge">En vivo</span></div>${aviso}</article>`;
}

async function tenants() {
  const filas = await api('/api/platform/tenants');
  $('#content').innerHTML = `<article class="card section-card">
    <div class="section-head"><h2>Nueva empresa</h2></div>
    <form id="tenant-form" class="inline-form">
      <label>Nombre<input name="name" required></label>
      <label>Slug<input name="slug" required pattern="[a-z0-9-]+"></label>
      <label>Dueño<input name="owner_name" required></label>
      <label>Correo<input name="owner_email" type="email" required></label>
      <label>Clave temporal<input name="owner_password" type="password" minlength="8" required></label>
      <label>Demo<select name="is_demo"><option value="true">Sí</option><option value="false">No</option></select></label>
      <button class="primary">Crear empresa</button>
    </form></article>
  <article class="card section-card"><div class="section-head"><h2>Empresas</h2>
    <span class="badge">${filas.length}</span></div>${tablaTenants(filas)}</article>`;
  $('#tenant-form').addEventListener('submit', async e => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.currentTarget));
    d.is_demo = d.is_demo === 'true';
    try {
      await api('/api/platform/tenants', { method: 'POST', body: JSON.stringify(d) });
      toast('Empresa creada');
      render('tenants');
    } catch (err) { toast(err.message, 'error'); }
  });
}

function tablaTenants(filas) {
  if (!filas.length) return '<div class="empty">Todavía no hay empresas.</div>';
  return `<table class="table"><thead><tr><th>EMPRESA</th><th>SLUG</th><th>ESTADO</th><th>TIPO</th></tr></thead>
    <tbody>${filas.map(x => `<tr><td><b>${esc(x.name)}</b></td><td>${esc(x.slug)}</td>
      <td><span class="badge">${esc(x.status)}</span></td><td>${x.is_demo ? 'Demo' : 'Real'}</td></tr>`).join('')}
    </tbody></table>`;
}

async function products() {
  const filas = await api(`/api/tenants/${state.tenantId}/products`);
  $('#content').innerHTML = `<article class="card section-card">
    <div class="section-head"><h2>Agregar producto</h2></div>
    <form id="product-form" class="inline-form">
      <label>SKU<input name="sku" required></label>
      <label>Nombre<input name="name" required></label>
      <label>Precio<input name="price" type="number" min="0" required></label>
      <label>Stock<input name="stock" type="number" min="0" required></label>
      <button class="primary">Guardar</button>
    </form></article>
  <article class="card section-card"><div class="section-head"><h2>Catálogo</h2>
    <span class="badge">${filas.length} productos</span></div>${tablaProductos(filas)}</article>`;
  $('#product-form').addEventListener('submit', async e => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.currentTarget));
    d.price = Number(d.price);
    d.stock = Number(d.stock);
    try {
      await api(`/api/tenants/${state.tenantId}/products`, { method: 'POST', body: JSON.stringify(d) });
      toast('Producto agregado');
      render('products');
    } catch (err) { toast(err.message, 'error'); }
  });
}

function tablaProductos(filas) {
  if (!filas.length) return '<div class="empty">Cargá el primer producto para empezar a vender.</div>';
  return `<table class="table"><thead><tr><th>SKU</th><th>PRODUCTO</th><th>PRECIO</th><th>STOCK</th><th>ESTADO</th></tr></thead>
    <tbody>${filas.map(x => `<tr><td>${esc(x.sku)}</td><td><b>${esc(x.name)}</b></td>
      <td>${money(x.price)}</td><td>${x.stock > 0 ? x.stock : '<b class="sin-stock">0</b>'}</td>
      <td><span class="badge">${x.active ? 'Activo' : 'Pausado'}</span></td></tr>`).join('')}
    </tbody></table>`;
}

// --- WhatsApp: cada empresa carga su propio número --------------------------

async function whatsapp() {
  const cuenta = await api(`/api/tenants/${state.tenantId}/whatsapp`).catch(err => {
    if (err.message.includes('todavía no cargó')) return null;
    throw err;
  });
  const estado = cuenta
    ? `<span class="badge">${cuenta.active ? 'Activo' : 'Desactivado'}</span>
       <span class="badge ${cuenta.verificado_en ? '' : 'badge-tibio'}">
         ${cuenta.verificado_en ? `Verificado ${esc(fecha(cuenta.verificado_en))}` : 'Sin verificar'}</span>`
    : '<span class="badge badge-tibio">Sin configurar</span>';

  $('#content').innerHTML = `<article class="card section-card">
    <div class="section-head"><h2>Tu número de WhatsApp</h2><div class="pills">${estado}</div></div>
    <p class="muted">Estos datos salen de tu cuenta de Meta (WhatsApp → API Setup). El token se guarda
      cifrado y no se muestra nunca más: si lo cambiás, pegá el nuevo; si dejás el campo vacío, se
      conserva el que ya está.</p>
    <form id="wa-form" class="form-columna">
      <label>Phone Number ID <small>(solo números, lo da Meta)</small>
        <input name="phone_number_id" required pattern="[0-9]+" value="${esc(cuenta?.phone_number_id || '')}">
      </label>
      <label>Número visible <small>(el que ven tus clientes)</small>
        <input name="display_phone_number" placeholder="595981123456" value="${esc(cuenta?.display_phone_number || '')}">
      </label>
      <label>WABA ID <small>(opcional, para plantillas)</small>
        <input name="waba_id" value="${esc(cuenta?.waba_id || '')}">
      </label>
      <label>Access token ${cuenta?.token_cargado ? `<small>(guardado: ${esc(cuenta.token_enmascarado)})</small>` : ''}
        <input name="access_token" type="password" autocomplete="off"
               placeholder="${cuenta?.token_cargado ? 'Dejar vacío para conservar el actual' : 'Pegá el token permanente'}"
               ${cuenta ? '' : 'required'}>
      </label>
      <label class="fila-check"><input type="checkbox" name="active" ${!cuenta || cuenta.active ? 'checked' : ''}>
        Atender mensajes en este número</label>
      <div class="acciones">
        <button class="primary" type="submit">Guardar</button>
        ${cuenta ? '<button class="secundario" type="button" id="wa-probar">Probar conexión</button>' : ''}
      </div>
    </form></article>
  <article class="card section-card"><div class="section-head"><h2>Cómo conseguir estos datos</h2></div>
    <ol class="pasos">
      <li>Entrá a <b>developers.facebook.com</b> → tu app → <b>WhatsApp → API Setup</b>.</li>
      <li>Registrá tu número real y copiá el <b>Phone Number ID</b> que aparece debajo (no el del número de prueba).</li>
      <li>Generá un <b>token permanente</b> desde un usuario del sistema. El token temporal vence en 24 h.</li>
      <li>Pegá los dos acá y tocá <b>Probar conexión</b>: si responde bien, el bot ya atiende.</li>
    </ol></article>`;

  $('#wa-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const cuerpo = {
      phone_number_id: f.get('phone_number_id'),
      display_phone_number: f.get('display_phone_number') || '',
      waba_id: f.get('waba_id') || null,
      active: f.get('active') === 'on',
    };
    const token = f.get('access_token');
    if (token) cuerpo.access_token = token;
    try {
      await api(`/api/tenants/${state.tenantId}/whatsapp`, { method: 'PUT', body: JSON.stringify(cuerpo) });
      toast('Número guardado');
      render('whatsapp');
    } catch (err) { toast(err.message, 'error'); }
  });

  const probar = $('#wa-probar');
  if (probar) probar.addEventListener('click', async () => {
    probar.disabled = true;
    probar.textContent = 'Probando…';
    try {
      const r = await api(`/api/tenants/${state.tenantId}/whatsapp/probar`, { method: 'POST' });
      toast(r.detalle, r.ok ? 'ok' : 'error');
      if (r.ok) render('whatsapp');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      probar.disabled = false;
      probar.textContent = 'Probar conexión';
    }
  });
}

// --- Pedidos: operar de verdad, no solo mirar ------------------------------

const ETIQUETA_ESTADO = {
  DRAFT: 'Borrador', PENDING_CONFIRMATION: 'A confirmar', PENDING_PAYMENT: 'Falta pago',
  CONFIRMED: 'Confirmado', PREPARING: 'Preparando', READY: 'Listo', ASSIGNED: 'Asignado',
  IN_TRANSIT: 'En camino', DELIVERED: 'Entregado', CANCELLED: 'Cancelado',
};
const ETIQUETA_ENTREGA = {
  PENDING: 'Pendiente', ASSIGNED: 'Asignada', PICKED_UP: 'Retirada', IN_TRANSIT: 'En camino',
  ARRIVED: 'En la puerta', DELIVERED: 'Entregada', FAILED: 'Fallida',
};

async function orders() {
  const filas = await api(`/api/tenants/${state.tenantId}/orders`);
  $('#content').innerHTML = `<article class="card section-card">
    <div class="section-head"><h2>Pedidos</h2><span class="badge">${filas.length}</span></div>
    ${tablaPedidos(filas)}</article><div id="detalle-pedido"></div>`;
  $$('[data-pedido]').forEach(b => b.addEventListener('click', () => abrirPedido(b.dataset.pedido)));
}

function tablaPedidos(filas) {
  if (!filas.length) return '<div class="empty">Los pedidos del bot y del panel aparecerán aquí.</div>';
  return `<table class="table"><thead><tr><th>PEDIDO</th><th>ORIGEN</th><th>ITEMS</th><th>TOTAL</th>
    <th>ESTADO</th><th></th></tr></thead>
    <tbody>${filas.map(x => `<tr>
      <td><b>${esc(x.id.slice(-8))}</b></td>
      <td>${esc(x.source)}</td>
      <td>${x.items.reduce((a, i) => a + i.quantity, 0)}</td>
      <td>${money(x.total)}</td>
      <td><span class="badge estado-${esc(x.status)}">${esc(ETIQUETA_ESTADO[x.status] || x.status)}</span></td>
      <td><button class="secundario chico" data-pedido="${esc(x.id)}">Gestionar</button></td>
    </tr>`).join('')}</tbody></table>`;
}

async function abrirPedido(orderId) {
  state.pedidoAbierto = orderId;
  const caja = $('#detalle-pedido');
  caja.innerHTML = '<article class="card section-card"><div class="empty">Cargando pedido…</div></article>';
  try {
    const [detalle, choferes] = await Promise.all([
      api(`/api/tenants/${state.tenantId}/orders/${orderId}`),
      api(`/api/tenants/${state.tenantId}/users?rol=DRIVER`).catch(() => []),
    ]);
    caja.innerHTML = vistaDetalle(detalle, choferes);
    caja.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    conectarAcciones(detalle);
  } catch (err) {
    caja.innerHTML = `<article class="card section-card"><div class="empty">${esc(err.message)}</div></article>`;
  }
}

const nombreChofer = (choferes, id) => (choferes.find(c => c.id === id) || {}).name || 'sin asignar';

function bloqueEstado(p) {
  if (!p.proximos_estados.length) return '<p class="muted">Este pedido ya está cerrado.</p>';
  const opciones = p.proximos_estados.map(e =>
    `<option value="${esc(e)}">${esc(ETIQUETA_ESTADO[e] || e)}</option>`).join('');
  return `<form id="form-estado" class="inline-form">
      <label>Mover a<select name="status">${opciones}</select></label>
      <button class="primary">Cambiar estado</button>
    </form>`;
}

function bloquePago(d) {
  const p = d.pedido;
  if (d.saldo <= 0) return '<p class="muted">Pedido totalmente pagado.</p>';
  if (['CANCELLED', 'DELIVERED'].includes(p.status)) return '<p class="muted">Pedido cerrado.</p>';
  return `<form id="form-pago" class="inline-form">
      <label>Medio<select name="provider">
        <option value="CASH">Efectivo</option><option value="TRANSFER">Transferencia</option>
        <option value="CARD">Tarjeta</option><option value="TIGO_MONEY">Tigo Money</option>
      </select></label>
      <label>Monto<input name="amount" type="number" min="1" max="${d.saldo}" value="${d.saldo}" required></label>
      <label>Estado<select name="status">
        <option value="APPROVED">Aprobado</option><option value="PENDING">Pendiente</option>
      </select></label>
      <button class="primary">Registrar pago</button>
    </form>`;
}

function bloqueEntrega(d, choferes) {
  if (d.entrega) {
    return `<p>Repartidor: <b>${esc(nombreChofer(choferes, d.entrega.driver_id))}</b> ·
        Estado: <span class="badge">${esc(ETIQUETA_ENTREGA[d.entrega.status] || d.entrega.status)}</span></p>
      <p class="muted">Link para el cliente:
        <a href="/seguimiento/${esc(d.entrega.tracking_token)}" target="_blank">abrir seguimiento</a></p>`;
  }
  if (d.pedido.status !== 'READY') {
    return '<p class="muted">Se asigna repartidor cuando el pedido está <b>Listo</b>.</p>';
  }
  const opciones = choferes.filter(c => c.active)
    .map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  if (!opciones) return '<p class="aviso">No tenés usuarios con rol DRIVER cargados.</p>';
  return `<form id="form-entrega" class="inline-form">
      <label>Repartidor<select name="driver_id">${opciones}</select></label>
      <button class="primary">Asignar</button>
    </form>`;
}

function vistaDetalle(d, choferes) {
  const p = d.pedido;
  const items = p.items.map(i =>
    `<tr><td>${esc(i.product_name)}</td><td>${i.quantity}</td><td>${money(i.unit_price)}</td>
     <td>${money(i.subtotal)}</td></tr>`).join('');
  const pagos = d.pagos.length
    ? d.pagos.map(x => `<li>${esc(x.provider)} · ${money(x.amount)} · <b>${esc(x.status)}</b></li>`).join('')
    : '<li class="muted">Sin pagos registrados.</li>';
  const rotuloSaldo = d.saldo > 0 ? `Falta ${money(d.saldo)}` : 'Pagado';

  return `<article class="card section-card" id="tarjeta-detalle">
    <div class="section-head"><h2>Pedido ${esc(p.id.slice(-8))}</h2>
      <span class="badge estado-${esc(p.status)}">${esc(ETIQUETA_ESTADO[p.status] || p.status)}</span></div>
    <div class="detalle-grid">
      <div>
        <h3>Items</h3>
        <table class="table compacta"><thead><tr><th>PRODUCTO</th><th>CANT</th><th>UNIT</th><th>SUBTOTAL</th></tr></thead>
          <tbody>${items}</tbody></table>
        <p class="totales">Subtotal ${money(p.subtotal)} · Envío ${money(p.shipping)} ·
          Descuento ${money(p.discount)} · <b>Total ${money(p.total)}</b></p>
        <p class="muted">${esc(p.address || 'Sin dirección cargada')}${p.requested_slot ? ` · ${esc(p.requested_slot)}` : ''}</p>
      </div>
      <div>
        <h3>Estado</h3>${bloqueEstado(p)}
        <h3>Cobros <span class="badge">${rotuloSaldo}</span></h3>
        <ul class="lista">${pagos}</ul>${bloquePago(d)}
        <h3>Entrega</h3>${bloqueEntrega(d, choferes)}
      </div>
    </div></article>`;
}

function conectarAcciones(d) {
  const orderId = d.pedido.id;
  const base = `/api/tenants/${state.tenantId}/orders/${orderId}`;
  enviar('#form-estado', datos => [`${base}/status`, { status: datos.status }], 'Estado actualizado');
  enviar('#form-pago', datos => [`${base}/payments`, {
    provider: datos.provider,
    amount: Number(datos.amount),
    status: datos.status,
    // Clave por pedido e instante: si el navegador reenvía, no se duplica el cobro.
    idempotency_key: `panel-${orderId}-${Date.now()}`,
  }], 'Pago registrado');
  enviar('#form-entrega', datos => [`${base}/delivery/assign`, { driver_id: datos.driver_id }], 'Repartidor asignado');
}

function enviar(selector, armar, mensajeOk) {
  const form = $(selector);
  if (!form) return;
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const boton = form.querySelector('button');
    boton.disabled = true;
    const [url, cuerpo] = armar(Object.fromEntries(new FormData(form)));
    try {
      await api(url, { method: 'POST', body: JSON.stringify(cuerpo) });
      toast(mensajeOk);
      const abierto = state.pedidoAbierto;
      await orders();
      if (abierto) await abrirPedido(abierto);
    } catch (err) {
      toast(err.message, 'error');
      boton.disabled = false;
    }
  });
}

boot();
