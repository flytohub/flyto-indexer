// Fixtures for weak_crypto (JS) + TLS rules.
const crypto = require('crypto')
const bcrypt = require('bcrypt')

function weakMd5(data) {
  return crypto.createHash('md5').update(data).digest('hex') // WEAK_HASH_MD5
}

function weakSha1(data) {
  return crypto.createHash('sha1').update(data).digest('hex') // WEAK_HASH_SHA1
}

function insecureToken() {
  return Math.random().toString(36) // INSECURE_RANDOM_MATHS
}

function weakAesEcb(key, plaintext) {
  return crypto.createCipheriv('aes-128-ecb', key, null) // WEAK_AES_ECB
}

function weakBcrypt(password) {
  return bcrypt.hashSync(password, 8) // BCRYPT_LOW_ROUNDS
}

// TLS verification off
const https = require('https')
const agent = new https.Agent({ rejectUnauthorized: false }) // TLS_VERIFY_DISABLED_JS
