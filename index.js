/**
 * RCVO API – index.js
 * Version simplifiée et fonctionnelle
 */

const express = require('express');
const helmet = require('helmet');
const cors = require('cors');
const dotenv = require('dotenv');
const jwt = require('jsonwebtoken');
// const mysql = require('mysql2/promise'); // décommentez si vous utilisez MariaDB/MySQL
// const AWS = require('aws-sdk');          // décommentez si vous utilisez AWS (SES, etc.)

// Charger les variables d’environnement depuis /opt/rcvo/src/api/.env (ou .env à la racine)
dotenv.config({ path: process.env.NODE_ENV === 'production'
  ? '/opt/rcvo/src/api/.env'
  : '.env'
});

const app = express();
app.use(helmet());
app.use(cors({ origin: '*', credentials: true }));
app.use(express.json());

// —————————————————————————————————————————
// Connexion à la base (exemple MariaDB) — décommentez si nécessaire
// let db;
// (async () => {
//   db = await mysql.createPool({
//     host:     process.env.DB_HOST,
//     port:     process.env.DB_PORT,
//     user:     process.env.DB_USER,
//     password: process.env.DB_PASS,
//     database: process.env.DB_NAME,
//     waitForConnections: true,
//     connectionLimit: 10,
//   });
// })();

// —————————————————————————————————————————
// Route de santé
app.get('/api/sync/health', (_req, res) => {
  res.status(200).json({ status: 'OK' });
});

// Route de connexion
app.post('/api/admin/login', async (req, res) => {
  const { email, password } = req.body;
  try {
    // — Votre logique de lookup et de comparaison de mot de passe
    // const [rows] = await db.query('SELECT * FROM admin WHERE email = ?', [email]);
    // if (rows.length === 0 || !await bcrypt.compare(password, rows[0].hash)) {
    //   return res.status(401).json({ message: 'Identifiants invalides' });
    // }

    // Pour test statique :
    if (email === 'test@example.com' && password === '123456') {
      // Créez un JWT réel ici si besoin :
      const token = jwt.sign({ email }, process.env.JWT_SECRET || 'secret', { expiresIn: '1h' });
      return res.status(200).json({ token });
    }

    return res.status(401).json({ message: 'Identifiants invalides' });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ message: 'Erreur serveur' });
  }
});

// Route d’inscription
app.post('/api/admin/register', async (req, res) => {
  const { email, password } = req.body;
  try {
    // — Votre logique d’enregistrement (hash du mot de passe, INSERT en base, etc.)
    // const hash = await bcrypt.hash(password, 10);
    // await db.query('INSERT INTO admin (email, hash) VALUES (?, ?)', [email, hash]);
    return res.status(201).json({ message: 'Utilisateur créé' });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ message: 'Erreur serveur' });
  }
});

// Démarrage du serveur
const PORT = process.env.PORT || 8081;
app.listen(PORT, () => {
  console.log(`API RCVO démarrée sur le port ${PORT}`);
});

