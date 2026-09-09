// Task card expand/collapse
document.querySelectorAll('.task-card[data-expandable]').forEach(card => {
  card.addEventListener('click', function (e) {
    // Don't toggle when clicking interactive elements
    if (e.target.closest('button, select, a, input, label')) return;
    card.classList.toggle('expanded');
  });
});
