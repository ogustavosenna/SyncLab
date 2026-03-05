# SyncLab - Guia Rapido para Testadores

## O que e o SyncLab?

O SyncLab sincroniza automaticamente videos de camera com audio externo (Zoom, Tascam, etc.) usando cross-correlation de audio. Ele gera um XML que voce importa no Premiere Pro com todos os clips ja alinhados na timeline.

---

## 1. Instalacao

1. Execute o arquivo `SyncLab_v1.3.1_Setup.exe`
2. Clique **Avancar** > **Avancar** > **Instalar**
3. Pronto! O SyncLab aparece no Menu Iniciar

> **IMPORTANTE - Aviso do Windows SmartScreen:**
> Na primeira vez que abrir o instalador, o Windows pode mostrar um alerta azul dizendo "O Windows protegeu o seu computador". Isso acontece porque o programa e novo e ainda nao tem certificado digital (estamos providenciando).
>
> Para continuar:
> 1. Clique em **"Mais informacoes"**
> 2. Clique em **"Executar assim mesmo"**
>
> Isso e seguro. O SyncLab e open source e o codigo pode ser verificado no GitHub.

---

## 2. Como Usar

### Passo 1: Abra o SyncLab
- Abra pelo Menu Iniciar ou pelo atalho na area de trabalho

### Passo 2: Adicione suas pastas
- **Pasta de videos:** Arraste ou selecione a pasta com seus arquivos de camera (.MOV, .MP4, .MXF)
- **Pasta de audio:** Arraste ou selecione a pasta com os arquivos do gravador externo (pastas ZOOM0001, ZOOM0002, etc.)

### Passo 3: Clique em Sync
- O SyncLab vai analisar todos os arquivos automaticamente
- Voce vera o progresso em tempo real na tela

### Passo 4: Resultado
- Ao terminar, o SyncLab mostra quantos clips foram sincronizados
- Um arquivo XML e gerado automaticamente

---

## 3. Importar no Premiere Pro

1. Abra o **Premiere Pro**
2. Va em **File > Import** (ou Ctrl+I)
3. Selecione o arquivo `.xml` gerado pelo SyncLab
4. O Premiere vai criar uma sequencia com todos os clips ja sincronizados
5. Os clips de video ficam em uma track, o audio externo em outra

---

## 4. Dicas

- **Multiplas cameras:** O SyncLab suporta projetos com varias cameras e varios gravadores
- **Formatos suportados:** MOV, MP4, MXF, AVI (video) e WAV (audio)
- **Velocidade:** O brute-force paralelo usa multiplos nucleos do processador para projetos grandes

---

## 5. Problemas?

Se algo nao funcionar como esperado:

1. Anote o que aconteceu (print da tela ajuda muito!)
2. Envie o feedback por um dos canais:
   - **GitHub Issues:** https://github.com/gustavosbox/SyncLab/issues
   - **Email:** gustavosbox@gmail.com

Inclua:
- Versao do SyncLab (aparece na janela do programa)
- Sistema operacional (Windows 10 ou 11)
- Quantos arquivos de video e audio voce usou
- O que aconteceu vs o que voce esperava
