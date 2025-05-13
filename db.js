// /opt/rcvo/src/api/db.js
const path = require('path');
const Database = require('better-sqlite3');

// Fichier de base, créé automatiquement s’il n’existe pas
const dbPath = path.join(__dirname, 'rcvo.db');
const db = new Database(dbPath);

// Création de la table admins si besoin
db.prepare(`
  CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    hashedPassword TEXT NOT NULL
  )
`).run();

module.exports = {
  // Simule execute(sql, params) renvoyant [rows]
  execute(sql, params = []) {
    const stmt = db.prepare(sql);
    if (/^\s*SELECT/i.test(sql)) {
      return [stmt.all(params)];
    } else {
      const info = stmt.run(params);
      return [{ lastID: info.lastInsertRowid, changes: info.changes }];
    }
  }
};
