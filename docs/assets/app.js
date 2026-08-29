(() => {
  const cards = [...document.querySelectorAll('.vacancy-card')];
  const contests = [...document.querySelectorAll('.contest-group')];
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
    let visibleContests = 0;
    contests.forEach(contest => {
      const show = Boolean(contest.querySelector('.vacancy-card:not([hidden])'));
      contest.hidden = !show;
      if (show) visibleContests += 1;
    });
    controls.scoreValue.value = controls.score.value;
    controls.count.textContent = `${visible} vaga(s) em ${visibleContests} concurso(s)`;
  }

  ['state', 'institution', 'eligibility', 'openOnly', 'newOnly']
    .forEach(name => controls[name].addEventListener('change', applyFilters));
  controls.search.addEventListener('input', applyFilters);
  controls.score.addEventListener('input', applyFilters);
  document.querySelector('#clear').addEventListener('click', () => {
    controls.search.value = ''; controls.state.value = ''; controls.institution.value = '';
    controls.eligibility.value = ''; controls.score.value = 0;
    controls.openOnly.checked = true; controls.newOnly.checked = false; applyFilters();
  });
  applyFilters();
})();
