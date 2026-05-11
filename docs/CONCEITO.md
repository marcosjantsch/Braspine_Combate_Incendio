# Conceito do sistema

## Plataforma de Auxilio ao Combate a Incendios Florestais

O sistema deve funcionar como uma plataforma operacional, nao como uma pagina unica com controles misturados.

O fluxo principal e:

1. Configurar a operacao
   - Selecionar empresas no menu lateral.
   - Selecionar dados GE, risco de incendio e atualizacao automatica.

2. Monitorar o territorio
   - Visualizar empresas, shapes ativos, base satelite, base com estradas, hotspots e risco de incendio no mapa operacional.
   - Aplicar GE apenas por acao explicita do usuario.
   - Evitar recalculo automatico enquanto a triangulacao estiver em uso.

3. Triangular ocorrencias
   - Criar torres por coordenada digitada ou clique no mapa.
   - Aceitar/recusar coordenada capturada antes de criar torre.
   - Rotacionar linhas de visada a partir da torre.
   - Excluir torres quando necessario.

4. Cadastrar dados estruturais
   - Area reservada para cadastro futuro de empresas, propriedades, usuarios e parametros.

## Estrutura de interface

- Menu lateral: configuracao persistente de Empresa e GE.
- Painel Operacional: resumo executivo da operacao atual.
- Mapa Operacional: mapa principal com camadas de monitoramento.
- Triangulacao: mapa + formulario/tabela de torres.
- Cadastro: manutencao futura de dados.

## Regras de processamento

- Earth Engine so deve recalcular quando o usuario clicar em Aplicar ou quando a atualizacao automatica estiver ligada e fora da area de Triangulacao.
- Durante a Triangulacao, as camadas GE devem permanecer congeladas.
- A ROI de GE vem do ultimo enquadramento visivel do mapa ou de uma ROI ja armazenada em session_state.
- A triangulacao trabalha em graus decimais internamente, mesmo quando a entrada e GMS ou UTM.
