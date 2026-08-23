(() => {
  const cards = [...document.querySelectorAll('.vacancy-card')];
  const controls = {
    search: document.querySelector('#search'), state: document.querySelector('#state'),
    institution: document.querySelector('#institution'), eligibility: document.querySelector('#eligibility'),
    score: document.querySelector('#score'), openOnly: document.querySelector('#open-only'),
    newOnly: document.querySelector('#new-only'), count: document.querySelector('#result-count'),
    scoreValue: document.querySelector('#score-value')
  };

  function applyFilters() {
    const query = controls.search.value.trim().toLocaleLowerCase('pt-BR');
    let visible = 0;
    cards.forEach(card => {
      const show = (!query || card.dataset.search.includes(query)) &&
        (!controls.state.value || card.dataset.state === controls.state.value) &&
        (!controls.institution.value || card.dataset.institution === controls.institution.value) &&
        (!controls.eligibility.value || card.dataset.eligibility === controls.eligibility.value) &&
        Number(card.dataset.score) >= Number(controls.score.value) &&
        (!controls.openOnly.checked || card.dataset.open === 'true') &&
        (!controls.newOnly.checked || card.dataset.new === 'true');
      card.hidden = !show;
      if (show) visible += 1;
    });
    controls.scoreValue.value = controls.score.value;
    controls.count.textContent = `${visible} resultado(s)`;
    document.querySelectorAll('.vacancy-group').forEach(group => {
      group.hidden = !group.querySelector('.vacancy-card:not([hidden])');
    });
  }

  Object.values(controls).filter(el => el && !['result-count', 'score-value'].includes(el.id))
    .forEach(el => el.addEventListener(el.type === 'search' ? 'input' : 'change', applyFilters));
  controls.search.addEventListener('input', applyFilters);
  controls.score.addEventListener('input', applyFilters);
  document.querySelector('#clear').addEventListener('click', () => {
    controls.search.value = ''; controls.state.value = ''; controls.institution.value = '';
    controls.eligibility.value = ''; controls.score.value = 0;
    controls.openOnly.checked = true; controls.newOnly.checked = false; applyFilters();
  });
  applyFilters();
})();
