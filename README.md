# Concursos Watch

Radar pessoal, automático e auditável de concursos públicos e processos seletivos para docentes. O projeto descobre oportunidades no PCI Concursos, lê anúncios e editais oficiais, mantém histórico versionado, classifica elegibilidade formal separadamente da aderência temática e publica um site responsivo no GitHub Pages.

> **Aviso importante**
> O sistema é uma ferramenta de triagem. A classificação “elegível” não substitui a leitura do edital oficial nem uma decisão da banca ou da instituição sobre equivalência de títulos e áreas. O PCI é fonte de descoberta, não a fonte jurídica definitiva.

## O que já está implementado

- descoberta tolerante a pequenas mudanças do HTML da página de professores;
- `requests.Session`, User-Agent identificável, timeout, retries limitados, backoff e intervalo entre requisições;
- filtro inicial conservador, privilegiando falsos positivos em vez de perder oportunidades;
- processamento de páginas novas, alteradas ou que precisam de revisão — sem baixar tudo novamente todos os dias;
- `first_seen`, `last_seen`, `last_checked`, hashes de listagem e conteúdo e histórico resumido de mudanças;
- datas brasileiras e fechamento automático; `CLOSING_SOON` significa prazo nos próximos 7 dias;
- leitura limitada e segura de páginas e PDFs oficiais, começando sempre pelos editais listados na notícia do PCI, com evidência e página de origem;
- separação das áreas de editais multiárea antes da classificação;
- classificação formal `YES`, `NO`, `UNCERTAIN` ou `UNKNOWN` com justificativa;
- pontuação temática transparente de 0 a 100, configurável;
- prioridade geográfica sem excluir nenhum estado;
- concursos agrupados por edital, cada um com sua própria tabela responsiva, acessível e filtrável;
- uma linha por vaga ou área nos editais multiárea, inclusive quando a aderência temática é zero;
- graduação e pós-graduação separadas em campos limpos;
- detalhes iguais em todas as vagas — como inscrições e remuneração — aparecem uma única vez abaixo do título; cada linha conserva somente os detalhes específicos;
- exibição inicial sem filtros ativos, preservando filtros opcionais para exploração posterior;
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
página individual ── parser ── fonte oficial ── PDF/HTML
      │                                  │ áreas + evidências por página
      │                                  ▼
      └────────────────────────── RuleBasedAnalyzer
                                         ├─ elegibilidade formal
                                         └─ aderência temática
      ▼
vacancies.json + official_documents.json + run_history.json
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
src/official.py            descoberta e leitura segura de editais HTML/PDF
src/classifier.py          regras formais e temáticas
src/storage.py             JSON atômico e validado
src/monitoring.py          status, rechecagem e mudanças
src/report.py              gerador do site
data/vacancies.json        vagas analisadas, inclusive encerradas
data/seen.json             índice leve de tudo que já foi descoberto
data/run_history.json      métricas das últimas 365 execuções
data/official_documents.json cache e auditoria das leituras oficiais
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

A etapa oficial revisa toda vaga aberta e tematicamente relevante, independentemente de a triagem inicial ser `YES`, `NO`, `UNCERTAIN` ou `UNKNOWN`. A notícia do PCI é sempre a primeira rota: PDFs diretos têm prioridade; quando o PCI oculta o endereço atrás de Turnstile, o bloqueio é registrado e o leitor tenta as fontes institucionais. Leituras conclusivas são renovadas em 14 dias; fontes ambíguas ou indisponíveis voltam à fila em 2 dias. Para diagnosticar uma instituição específica sem baixar páginas do PCI:

```bash
python monitor.py --max-fetch 0 --force-official --official-match UEM --max-official 1
```

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

`UNKNOWN` significa que o anúncio ou o bloco correspondente do edital não ofereceu evidência suficiente. Ele pode permanecer mesmo após o PDF ter sido lido. `UNCERTAIN` significa que há uma expressão ambígua — por exemplo “áreas afins” ou uma referência à Área de Avaliação Interdisciplinar — que requer interpretação humana.

Em editais multiárea, cada área é analisada como uma sub-vaga. Requisitos de Agronomia, Engenharia e Educação, por exemplo, nunca são concatenados para decidir um único resultado. Cada concurso recebe uma tabela própria com todas as vagas extraídas, e cada requisito permanece ligado à página correspondente do edital. A página abre sem filtros ativos.

## GitHub Actions e Pages

O workflow tem somente os gatilhos `schedule` e `workflow_dispatch`; commits do bot não criam loops. Ele:

1. instala dependências;
2. executa o monitor;
3. roda todos os testes;
4. valida os quatro JSON e o HTML;
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

- Nem toda instituição fornece um link direto ou um PDF textual; nesses casos o resultado continua auditavelmente `UNKNOWN`/`UNCERTAIN`.
- O PCI atualmente só libera alguns endereços de PDF após Cloudflare Turnstile. O sistema registra o edital e oferece a notícia para abertura manual, mas não burla CAPTCHA; em seguida tenta localizar o mesmo documento na fonte institucional.
- Extração por regras não interpreta todas as construções jurídicas possíveis.
- Tabelas de editais com estrutura inédita podem exigir uma nova regra de segmentação; o sistema evita agregar requisitos quando não consegue delimitá-los com segurança.
- O link externo encontrado pode apontar para inscrições, e não diretamente para o PDF do edital.
- GitHub pode atrasar schedules em momentos de alta carga; o minuto 17 reduz a disputa no início da hora.
- Em repositórios públicos sem atividade, o GitHub pode desativar schedules após período prolongado; uma execução manual os reativa conforme as políticas da plataforma.

Em todos os casos, o card mostra a evidência encontrada e oferece acesso à fonte para revisão humana.
