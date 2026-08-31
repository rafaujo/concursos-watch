(() => {
  const cards = [...document.querySelectorAll('.vacancy-card')];
  const contests = [...document.querySelectorAll('.contest-group')];
  const controls = {
    search: document.querySelector('#search'), state: document.querySelector('#state'),
    institution: document.querySelector('#institution'),
    institutionType: document.querySelector('#institution-type'),
    course: document.querySelector('#course'),
    eligibility: document.querySelector('#eligibility'),
    score: document.querySelector('#score'), openOnly: document.querySelector('#open-only'),
    newOnly: document.querySelector('#new-only'), count: document.querySelector('#result-count'),
    scoreValue: document.querySelector('#score-value')
  };

  // The page opens filtered to higher education, so "Limpar filtros" restores
  // what the reader arrived at rather than dropping them into every municipal
  // basic-education posting.
  const defaultInstitutionType = controls.institutionType ? controls.institutionType.value : '';

  function applyFilters() {
    const query = controls.search.value.trim().toLocaleLowerCase('pt-BR');
    const type = controls.institutionType ? controls.institutionType.value : '';
    let visible = 0;
    cards.forEach(card => {
      const show = (!query || card.dataset.search.includes(query)) &&
        (!controls.state.value || card.dataset.state === controls.state.value) &&
        (!controls.institution.value || card.dataset.institution === controls.institution.value) &&
        (!type || card.dataset.institutionType === type) &&
        (!controls.course.value || card.dataset.course === controls.course.value) &&
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

  ['state', 'institution', 'institutionType', 'course', 'eligibility', 'openOnly', 'newOnly']
    .forEach(name => {
      if (controls[name]) controls[name].addEventListener('change', applyFilters);
    });
  controls.search.addEventListener('input', applyFilters);
  controls.score.addEventListener('input', applyFilters);
  document.querySelector('#clear').addEventListener('click', () => {
    controls.search.value = ''; controls.state.value = ''; controls.institution.value = '';
    controls.course.value = ''; controls.eligibility.value = ''; controls.score.value = 0;
    if (controls.institutionType) controls.institutionType.value = defaultInstitutionType;
    controls.openOnly.checked = false; controls.newOnly.checked = false; applyFilters();
  });
  applyFilters();
})();
