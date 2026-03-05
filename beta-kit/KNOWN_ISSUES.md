# SyncLab v1.3.1 - Problemas Conhecidos

## Limitacoes atuais

1. **Exporta apenas para Premiere Pro**
   - O formato de saida e XML (xmeml v5) compativel com Premiere Pro
   - DaVinci Resolve sera suportado em versao futura

2. **Windows SmartScreen mostra alerta**
   - O instalador ainda nao tem certificado digital
   - Clique em "Mais informacoes" > "Executar assim mesmo"
   - Estamos em processo de obter code signing gratuito via SignPath.io

3. **Primeira execucao pode ser mais lenta**
   - O cache de audio precisa ser construido na primeira vez
   - Execucoes seguintes com os mesmos arquivos serao mais rapidas

4. **Interface ainda basica**
   - A UI esta funcional mas sera melhorada nas proximas versoes
   - Aceitamos sugestoes de design!

## Requisitos do sistema

- Windows 10 ou 11 (64-bit)
- Pelo menos 4 GB de RAM
- Espaco em disco para arquivos temporarios (~500 MB durante processamento)

## O que NAO e um bug

- **Videos sem match:** Se um video nao tem audio gravado simultaneamente pelo gravador externo, ele aparecera como "unmatched" — isso e esperado
- **Confianca baixa:** Matches com confianca baixa usam timestamp como fallback — isso e intencional para garantir que todos os clips aparecam na timeline
