/**
 * RCVO API - index.js
 * Version simplifiée et fonctionnelle
 */

const express = require('express');
const helmet = require('helmet');
const cors = require('cors');
const dotenv = require('dotenv');
const jwt = require('jsonwebtoken');
const mysql = require('mysql2/promise');
const AWS = require('aws-sdk');

// Charger .env
dotenv.config({ path: '/opt/rcvo/src/api/.env' });

const app = express();
app.use(helmet());
app.use(cors());
app.use(express.json());

// Connexion MariaDB
let db;
(async () => {
  db = await mysql.createPool({
    host: process.env.DB_HOST,
    port: +process.env.DB_PORT,
    user: process.env.DB_USER,
    password: process.env.DB_PASS,
    database: process.env.DB_NAME,
    waitForConnections: true,
    connectionLimit: 10,
  });
})();

// Config AWS SES
const ses = new AWS.SES({ region: 'eu-west-3' });

// Route de santé
app.get('/api/sync/health', (req, res) => {
  res.status(200).json({ status: 'OK' });
});

// Exemple d’enregistrement d’un admin (simplifié)
app.post('/api/admin/register', async (req, res) => {
  try {
    const { email, password } = req.body;
    // Ici, hasher le mot de passe en prod…
    const [result] = await db.execute(
      'INSERT INTO admins (email, password) VALUES (?, ?)',
      [email, password]
    );
    return res.status(201).json({ id: result.insertId, email });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur interne' });
  }
});

// Confirmation de compte admin
app.post('/api/admin/confirm', async (req, res) => {
  const { token } = req.body;
  try {
    const payload = jwt.verify(token, process.env.JWT_SECRET);
    await db.execute(
      'UPDATE admins SET confirmed = TRUE WHERE id = ?',
      [payload.sub]
    );
    return res.json({ message: 'Compte confirmé' });
  } catch (err) {
    console.error(err);
    return res.status(400).json({ error: 'Token invalide' });
  }
});

// Envoi de mail de confirmation (simplifié)
app.post('/api/admin/send-confirm/:id', async (req, res) => {
  const adminId = req.params.id;
  try {
    // Récupérer l’admin
    const [rows] = await db.execute(
      'SELECT email FROM admins WHERE id = ?',
      [adminId]
    );
    if (!rows.length) return res.status(404).json({ error: 'Admin non trouvé' });
    const email = rows[0].email;
    // Générer token
    const confirmToken = jwt.sign({ sub: adminId }, process.env.JWT_SECRET, {
      expiresIn: '24h',
    });
    const confirmUrl =
      'https://' +
      process.env.API_HOST + // définie dans .env si nécessaire
      '/confirm.html?token=' +
      confirmToken;

    // Préparer email
    const emailParams = {
      Destination: { ToAddresses: [email] },
      Message: {
        Subject: { Data: 'Confirmez votre compte Admin Rcvo' },
        Body: {
          Text: {
            Data:
              'Bonjour,\n\n' +
              'Cliquez sur ce lien pour activer votre compte administrateur : ' +
              confirmUrl +
              '\n\n' +
              'Ce lien expire dans 24 heures.',
          },
        },
      },
      Source: 'noreply@rcvo-crm-auto.com',
    };

    await ses.sendEmail(emailParams).promise();
    return res.status(200).json({ message: 'Email envoyé à ' + email });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Impossible d’envoyer l’email' });
  }
});

// Démarrage
const port = +process.env.PORT || 3000;
app.listen(port, () => {
  console.log(`RCVO API démarrée sur le port ${port}`);
});
