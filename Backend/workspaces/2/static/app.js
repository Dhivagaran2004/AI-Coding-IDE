document.addEventListener('DOMContentLoaded', () => {
    fetch('/api/employees')
        .then(response => response.json())
        .then(data => {
            const list = document.getElementById('employee-list');
            data.forEach(emp => {
                const li = document.createElement('li');
                li.textContent = `${emp.id}: ${emp.name} - ${emp.position}`;
                list.appendChild(li);
            });
        })
        .catch(err => console.error(err));
});