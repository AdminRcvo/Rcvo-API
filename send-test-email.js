#!/usr/bin/env node
const AWS = require('aws-sdk');

// Crée directement le client SES avec la région ASCII
const ses = new AWS.SES({ region: 'eu-west-3' });

const params = {
  Source: 'laurent.g40@live.fr',
  Destination: { ToAddresses: ['laurent.g40@live.fr'] },
  Message: {
    Subject: { Data: 'Test email from Rcvo' },
    Body: { Text: { Data: 'This is a test email via AWS SES.' } }
  }
};

ses.sendEmail(params).promise()
  .then(data => console.log('Email sent, MessageId:', data.MessageId))
  .catch(err => console.error('SES Error:', err));
