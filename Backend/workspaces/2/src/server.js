const express = require('express');
const employeeRouter = require('./routes/employee');
const app = express();
app.use(express.json());
app.use('/employees', employeeRouter);
module.exports = app;