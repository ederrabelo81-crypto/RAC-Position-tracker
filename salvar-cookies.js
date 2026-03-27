/**
 * ══════════════════════════════════════════════════════════════
 * COOKIE HELPER — Salvar e carregar cookies do browser
 * ══════════════════════════════════════════════════════════════
 * Usado para Shopee (exige login) e outros sites que precisam
 * de sessão autenticada.
 *
 * Fluxo:
 *   1. Rodar: node salvar-cookies.js shopee
 *   2. Browser abre, você faz login manualmente
 *   3. Pressiona Enter no terminal quando estiver logado
 *   4. Cookies salvos em ./cookies/shopee.json
 *   5. Coletas futuras carregam os cookies automaticamente
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const readline = require('readline');

const COOKIES_DIR = './cookies';
const SITES = {
  shopee: 'https://shopee.com.br',
  casasbahia: 'https://www.casasbahia.com.br',
  google: 'https://www.google.com.br',
};

async function askUser(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => rl.question(question, answer => { rl.close(); resolve(answer); }));
}

async function saveCookies() {
  const site = process.argv[2];
  if (!site || !SITES[site]) {
    console.log('Uso: node salvar-cookies.js <site>');
    console.log('Sites disponíveis:', Object.keys(SITES).join(', '));
    process.exit(1);
  }

  if (!fs.existsSync(COOKIES_DIR)) fs.mkdirSync(COOKIES_DIR, { recursive: true });

  console.log(`\nAbrindo ${SITES[site]} no browser...`);
  console.log('Faça login normalmente. Quando estiver logado, volte aqui e pressione ENTER.\n');

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
  });
  const page = await context.newPage();
  await page.goto(SITES[site], { waitUntil: 'domcontentloaded' });

  await askUser('>>> Pressione ENTER quando estiver logado...');

  // Salvar cookies
  const cookies = await context.cookies();
  const cookiePath = path.join(COOKIES_DIR, `${site}.json`);
  fs.writeFileSync(cookiePath, JSON.stringify(cookies, null, 2));

  // Salvar localStorage também (útil para alguns sites)
  const storage = await page.evaluate(() => JSON.stringify(localStorage));
  fs.writeFileSync(path.join(COOKIES_DIR, `${site}_storage.json`), storage);

  console.log(`\n✓ ${cookies.length} cookies salvos em ${cookiePath}`);
  console.log('  As próximas coletas vão usar esses cookies automaticamente.');
  console.log('  Se o login expirar, rode este script novamente.\n');

  await browser.close();
}

/**
 * Carrega cookies salvos em um contexto Playwright
 */
async function loadCookies(context, site) {
  const cookiePath = path.join(COOKIES_DIR, `${site}.json`);
  if (!fs.existsSync(cookiePath)) return false;

  try {
    const cookies = JSON.parse(fs.readFileSync(cookiePath, 'utf8'));
    await context.addCookies(cookies);
    return true;
  } catch (_) {
    return false;
  }
}

/**
 * Verifica se tem cookies salvos para um site
 */
function hasCookies(site) {
  return fs.existsSync(path.join(COOKIES_DIR, `${site}.json`));
}

// Executar se chamado diretamente
if (require.main === module) {
  saveCookies().catch(err => {
    console.error('Erro:', err.message);
    process.exit(1);
  });
}

module.exports = { loadCookies, hasCookies };
