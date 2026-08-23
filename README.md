# Concursos Watch

Radar pessoal, automático e auditável de concursos públicos e processos seletivos para docentes. O projeto descobre oportunidades no PCI Concursos, lê anúncios individuais, mantém histórico versionado, classifica elegibilidade formal separadamente da aderência temática e publica um site responsivo no GitHub Pages.

> **Aviso importante**
> O sistema é uma ferramenta de triagem. A classificação “elegível” não substitui a leitura do edital oficial nem uma decisão da banca ou da instituição sobre equivalência de títulos e áreas. O PCI é fonte de descoberta, não a fonte jurídica definitiva.

## O que já está implementado

- descoberta tolerante a pequenas mudanças do HTML da página de professores;
- `requests.Session`, User-Agent identificável, timeout, retries limitados, backoff e intervalo entre requisições;
- filtro inicial conservador, privilegiando falsos positivos em vez de perder oportunidades;
- processamento de páginas novas, alteradas ou que precisam de revisão — sem baixar tudo novamente todos os dias;
- `first_seen`, `last_seen`, `last_checked`, hashes de listagem e conteúdo e histórico resumido de mudanças;
- datas brasileiras e fechamento automático; `CLOSING_SOON` significa prazo nos próximos 7 dias;
- classificação formal `YES`, `NO`, `UNCERTAIN` ou `UNKNOWN` com justificativa;
- pontuação temática transparente de 0 a 100, configurável;
- prioridade geográfica sem excluir nenhum estado;
- relatório HTML responsivo, acessível e filtrável, sem framework pesado;
- testes das regras de aceitação, persistência, parser e relatório;
- execução diária às 08:17 em `America/Sao_Paulo`, disparo manual, commit condicional e deploy no Pages.

## Regra CAPES que não pode ser simplificada

O perfil está centralizado em [`config.py`](config.py). Seus conceitos são armazenados separadamente:

| Conceito | Valor |
|---|---|
| Área de Avaliação CAPES | Ciências Ambientais |
| Grande Área CAPES | Multidisciplinar |
| Característica acadêmica do programa | Interdisciplinar |

O fato de o PPG-CiAC ter caráter interdisciplinar **não** significa que pertença à Área de Avaliação Interdisciplinar da CAPES. Uma exigência dessa Área de Avaliação recebe `UNCERTAIN`, nunca `YES` automático. A suíte contém um teste dedicado para impedir regressões nessa regra.

## Arquitetura

```text
PCI /professores
      │ descoberta leve
      ▼
seen.json ── URL nova, hash alterado ou revisão vencida?
      │ sim
      ▼
página individual ── parser ── RuleBasedAnalyzer
      │                         ├─ elegibilidade formal
      │                         └─ aderência temática
      ▼
vacancies.json + run_history.json
      │
      ▼
docs/index.html ── GitHub Pages
```

`src/interfaces.py` define dois contratos pequenos:

- `VacancySource`: permite adicionar DOU, páginas de universidades ou outras fontes;
- `VacancyAnalyzer`: permite adicionar futuramente um `LLMAnalyzer` sem trocar o crawler ou a persistência.

A implementação atual usa somente `PCIConcursosSource` e `RuleBasedAnalyzer`. Não há API de IA nem custo obrigatório.

## Estrutura de arquivos

```text
monitor.py                 orquestra uma execução
config.py                  perfil, pesos, thresholds e política de rede
src/pci.py                 cliente e parser da listagem PCI
src/parser.py              detalhe, requisitos, texto e datas
src/classifier.py          regras formais e temáticas
src/storage.py             JSON atômico e validado
src/monitoring.py          status, rechecagem e mudanças
src/report.py              gerador do site
data/vacancies.json        vagas analisadas, inclusive encerradas
data/seen.json             índice leve de tudo que já foi descoberto
data/run_history.json      métricas das últimas 365 execuções
docs/                      site publicado
tests/                     testes e fixtures representativas do PCI
.github/workflows/daily.yml automação e publicação
```

## Execução local

Requer Python 3.11 ou mais recente.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m pytest
python monitor.py
```

Para uma verificação rápida e respeitosa da integração, sem processar toda a primeira carga:

```bash
python monitor.py --max-fetch 3 --delay 0.25
```

Itens que ficaram na fila porque `--max-fetch` foi usado continuam com `processed: false` e serão analisados na execução seguinte. Para logs de diagnóstico, acrescente `--verbose`.

## Estado, rechecagem e mudanças

`seen.json` não é apenas uma lista de URLs. Cada item registra hash da listagem, primeira/última aparição, última consulta detalhada, estado e eventual erro. Uma página é consultada quando:

1. é nova e passa pelo filtro conservador;
2. os metadados da listagem mudam;
3. está aberta e a janela configurada de revisão venceu;
4. está próxima do prazo e ainda não foi revisada naquele dia;
5. uma tentativa anterior falhou.

Uma falha individual gera warning e fica registrada, sem abortar as outras vagas. Já a ausência total de links na listagem aborta claramente a execução, pois normalmente significa mudança estrutural no PCI. Escritas JSON usam arquivo temporário e substituição atômica; JSON corrompido causa erro explícito em vez de ser sobrescrito silenciosamente.

Mudanças em prazo, remuneração, título e requisitos são registradas. Se apenas o hash geral mudar, o histórico informa `conteúdo alterado`.

## Classificação

A elegibilidade formal e a aderência temática são calculadas de maneira independente. Por isso uma vaga de Gestão Socioambiental pode ter aderência 100 e elegibilidade `NO` quando exigir exclusivamente Engenharia Ambiental.

Os pesos ficam em `THEMATIC_WEIGHTS`, e os destaques em `STRONG_YES_SCORE` e `STRONG_UNCERTAIN_SCORE`, todos em `config.py`. As frases originais encontradas para graduação, mestrado e doutorado são preservadas em campos `*_requirement_raw`.

`UNKNOWN` significa que o anúncio não ofereceu evidência suficiente. `UNCERTAIN` significa que há uma expressão ambígua — por exemplo “áreas afins” ou uma referência à Área de Avaliação Interdisciplinar — que requer interpretação humana.

## GitHub Actions e Pages

O workflow tem somente os gatilhos `schedule` e `workflow_dispatch`; commits do bot não criam loops. Ele:

1. instala dependências;
2. executa o monitor;
3. roda todos os testes;
4. valida os três JSON e o HTML;
5. empacota o site;
6. commita `data/` e `docs/` apenas se mudaram;
7. publica o artefato no GitHub Pages.

Depois de enviar o repositório ao GitHub, a única configuração manual normalmente necessária é abrir **Settings → Pages → Build and deployment → Source** e selecionar **GitHub Actions**. Em seguida use **Actions → Atualizar radar → Run workflow** para a primeira carga. O endereço será:

```text
https://<usuario>.github.io/concursos-watch/
```

O token padrão recebe escrita em conteúdo somente no job que commita. O job de deploy recebe apenas `pages: write` e `id-token: write`.

## Alterar o perfil ou a política

Edite apenas `config.py` para:

- mudar formação e classificação CAPES em `PROFILE`;
- ajustar pesos em `THEMATIC_WEIGHTS`;
- mudar estados prioritários em `GEOGRAPHIC_PRIORITIES`;
- ajustar destaque, prazo de fechamento, retenção e frequência de rechecagem;
- mudar pausa, timeout ou retries do crawler.

Ao alterar regras formais, atualize os testes antes de publicar. Regras jurídicas complexas devem preferir `UNCERTAIN` a uma equivalência presumida.

## Adicionar uma fonte

Crie uma classe que implemente `VacancySource.discover()` e `VacancySource.fetch()`, normalize o resultado para o mesmo esquema e registre-a no orquestrador. Não acople seletores HTML ao classificador; cada fonte deve conter seus próprios seletores e preservar evidências textuais.

## Adicionar análise por LLM no futuro

Implemente `VacancyAnalyzer.analyze(vacancy, profile)`. Uma estratégia segura é manter as regras atuais como primeira etapa e usar o LLM apenas para complementar `UNKNOWN`/`UNCERTAIN`, guardando modelo, prompt, resposta estruturada e evidências. Nenhum resultado de LLM deve apagar o texto original do requisito.

## Limitações conhecidas

- Resumos do PCI podem omitir requisitos que existem apenas no PDF oficial.
- PDFs protegidos por verificação humana não são contornados; o crawler não tenta burlar CAPTCHA ou bloqueios.
- Extração por regras não interpreta todas as construções jurídicas possíveis.
- Um anúncio com várias vagas heterogêneas pode ser representado inicialmente como um único registro.
- O link externo encontrado pode apontar para inscrições, e não diretamente para o PDF do edital.
- GitHub pode atrasar schedules em momentos de alta carga; o minuto 17 reduz a disputa no início da hora.
- Em repositórios públicos sem atividade, o GitHub pode desativar schedules após período prolongado; uma execução manual os reativa conforme as políticas da plataforma.

Em todos os casos, o card mostra a evidência encontrada e oferece acesso à fonte para revisão humana.
