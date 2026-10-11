// AddisMart: a small shop with planted bugs, the second practice app for
// Mirmera. No dependencies: run `node practice-app/shop.mjs` and open
// http://localhost:4200. Orders live in memory and reset on restart.
//
// Don't read the code too closely before testing: the bugs are listed in
// practice-app/SHOP-ANSWERS.md for afterwards.
import http from 'node:http'

const PORT = Number(process.env.PORT ?? 4200)
const PRODUCTS = {
  coffee: { name: 'Coffee beans, 250 g', price: 350 },
  basket: { name: 'Injera basket', price: 120 },
  berbere: { name: 'Berbere, 500 g', price: 180 },
}
const orders = [] // { id, product, qty, name, email, phone, date, total }

const page = (title, body) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>${title} · AddisMart</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #f7f5f2; color: #2a2118 }
  header { background: #5a2d0c; color: white; padding: 14px 32px; display: flex; gap: 24px; align-items: center }
  header a { color: #ffe0c2; text-decoration: none } header strong { margin-right: auto }
  main { max-width: 560px; margin: 32px auto; background: white; padding: 28px 32px; border-radius: 10px; box-shadow: 0 1px 3px #0002 }
  label { display: block; font-size: 14px; margin: 14px 0 4px }
  input, select { width: 100%; padding: 8px 10px; font-size: 15px; box-sizing: border-box; border: 1px solid #cfc6bb; border-radius: 6px }
  button { margin-top: 20px; background: #b4531a; color: white; border: 0; padding: 10px 22px; font-size: 15px; border-radius: 6px; cursor: pointer }
  [role=status] { margin-top: 16px; font-weight: 600 } .ok { color: #1b7a3a } .bad { color: #b3261e }
  .total { margin-top: 14px; font-size: 16px } li { margin: 6px 0 }
</style></head>
<body><header><strong>AddisMart</strong><a href="/">Shop</a><a href="/order">Order</a><a href="/orders">My orders</a></header>
<main>${body}</main></body></html>`

const HOME = page('Shop', `
  <h1>AddisMart</h1>
  <p>Fresh from Addis, delivered to your door.</p>
  <ul>${Object.entries(PRODUCTS).map(([id, p]) => `<li>${p.name}: ${p.price} Birr · <a href="/order?product=${id}">Order</a></li>`).join('')}</ul>`)

const ORDER = page('Order', `
  <h1>Place an order</h1>
  <form id="order" novalidate>
    <label for="product">Product</label>
    <select id="product">${Object.entries(PRODUCTS).map(([id, p]) => `<option value="${id}">${p.name}</option>`).join('')}</select>
    <label for="qty">Quantity</label><input id="qty" type="number" value="1">
    <p class="total" id="total"></p>
    <label for="name">Full name</label><input id="name" autocomplete="name">
    <label for="email">Email</label><input id="email" type="email">
    <label for="phone">Phone number</label><input id="phone" type="tel" placeholder="+251 9xx xxx xxx">
    <label for="date">Delivery date</label><input id="date" type="date">
    <button type="submit">Place order</button>
  </form>
  <p role="status" id="result"></p>
  <script>
    const PRICES = ${JSON.stringify(Object.fromEntries(Object.entries(PRODUCTS).map(([id, p]) => [id, p.price])))}
    const v = (id) => document.getElementById(id).value.trim()
    const pre = new URLSearchParams(location.search).get('product')
    if (pre && PRICES[pre]) document.getElementById('product').value = pre
    function total() {
      const qty = Number(v('qty')) || 0
      return PRICES[v('product')] * (qty >= 3 ? qty - 1 : qty)
    }
    function showTotal() { document.getElementById('total').textContent = 'Total: ' + total() + ' Birr' }
    for (const id of ['product', 'qty']) document.getElementById(id).addEventListener('input', showTotal)
    showTotal()
    document.getElementById('order').addEventListener('submit', async (e) => {
      e.preventDefault()
      const out = document.getElementById('result')
      const bad = (text) => { out.className = 'bad'; out.textContent = text }
      const qty = Number(v('qty'))
      if (!Number.isInteger(qty) || qty < 1) return bad('Quantity must be between 1 and 10.')
      if (!v('name')) return bad('Please enter your full name.')
      if (!v('email').includes('@')) return bad('Please enter a valid email address.')
      if (!v('phone')) return bad('Phone number is required.')
      const today = new Date().toISOString().slice(0, 10)
      if (!v('date')) return bad('Please choose a delivery date.')
      if (v('date') < today) return bad("The delivery date can't be in the past.")
      out.className = ''; out.textContent = 'Placing your order…'
      const order = { product: v('product'), qty, name: v('name'), email: v('email'), phone: v('phone'), date: v('date'), total: total() }
      const r = await fetch('/api/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(order) })
      if (!r.ok) return bad('Something went wrong. Please try again.')
      sessionStorage.setItem('order', JSON.stringify({ ...order, id: (await r.json()).id }))
      location.href = '/order/confirmation'
    })
  </script>`)

const CONFIRMATION = page('Order placed', `
  <h1>Order placed</h1>
  <p role="status" class="ok">Thank you! Your order is placed.</p>
  <p id="summary"></p>
  <p><a href="/orders">See My orders</a></p>
  <script>
    const PRODUCTS = ${JSON.stringify(PRODUCTS)}
    const order = JSON.parse(sessionStorage.getItem('order'))
    sessionStorage.removeItem('order')
    document.getElementById('summary').textContent =
      'Order ' + order.id + ': ' + PRODUCTS[order.product].name + ' × ' + order.qty + ', total ' + order.total + ' Birr, for ' + order.name
  </script>`)

const ORDERS = page('My orders', `
  <h1>My orders</h1>
  <p id="count">Loading…</p>
  <ul id="list"></ul>
  <script>
    fetch('/api/orders').then((r) => r.json()).then((data) => {
      document.getElementById('count').textContent = data.orders.length + ' order(s)'
      document.getElementById('list').innerHTML = data.orders
        .map((o) => '<li>' + o.name + ': ' + o.productName + ' × ' + o.qty + ', ' + o.total + ' Birr</li>').join('')
    })
  </script>`)

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(body))
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = ''
    req.on('data', (c) => (data += c))
    req.on('end', () => { try { resolve(JSON.parse(data || '{}')) } catch { resolve({}) } })
  })
}

const html = (res, body) => res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }).end(body)

http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)
  if (req.method === 'GET' && url.pathname === '/') return html(res, HOME)
  if (req.method === 'GET' && url.pathname === '/order') return html(res, ORDER)
  if (req.method === 'GET' && url.pathname === '/order/confirmation') return html(res, CONFIRMATION)
  if (req.method === 'GET' && url.pathname === '/orders') return html(res, ORDERS)
  if (req.method === 'POST' && url.pathname === '/api/orders') {
    const body = await readBody(req)
    if (!PRODUCTS[body.product]) return json(res, 400, { error: 'unknown product' })
    const order = { ...body, id: orders.length + 1, productName: PRODUCTS[body.product].name }
    orders.push(order)
    return json(res, 201, { id: order.id })
  }
  if (req.method === 'GET' && url.pathname === '/api/orders') return json(res, 200, { orders })
  res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Not found')
}).listen(PORT, () => console.log(`AddisMart shop demo on http://localhost:${PORT}`))
