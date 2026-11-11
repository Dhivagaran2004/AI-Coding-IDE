const express = require('express');
const cors = require('cors');
const app = express();
app.use(cors());
app.use(express.json());

let employees = [
  { id: 1, name: 'Alice', position: 'Developer' },
  { id: 2, name: 'Bob', position: 'Designer' }
];

app.get('/employees', (req, res) => {
  res.json(employees);
});

app.post('/employees', (req, res) => {
  const newEmp = { id: Date.now(), ...req.body };
  employees.push(newEmp);
  res.status(201).json(newEmp);
});

app.put('/employees/:id', (req, res) => {
  const id = parseInt(req.params.id);
  const index = employees.findIndex(e => e.id === id);
  if (index === -1) return res.status(404).send('Not found');
  employees[index] = { ...employees[index], ...req.body };
  res.json(employees[index]);
});

app.delete('/employees/:id', (req, res) => {
  const id = parseInt(req.params.id);
  employees = employees.filter(e => e.id !== id);
  res.status(204).send();
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log(`Backend running on port ${PORT}`));