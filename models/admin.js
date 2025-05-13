const db = require('../db');

async function getAdminByEmail(email) {
  const [rows] = await db.execute(
    'SELECT id, email, hashedPassword FROM admins WHERE email = ?',
    [email]
  );
  return rows[0];
}

async function addAdmin(email, hashedPassword) {
  const insertQuery = 'INSERT INTO admins (email, hashedPassword) VALUES (?, ?)';
  await db.execute(insertQuery, [email, hashedPassword]);

  const [rows] = await db.execute(
    'SELECT id, email FROM admins WHERE email = ?',
    [email]
  );
  return rows[0];
}

module.exports = { getAdminByEmail, addAdmin };
