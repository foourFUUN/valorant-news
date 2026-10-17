# Valorant News

Envia notícias do **THESPIKE.GG (BR)** e do **VLR.gg** para um canal do Discord via webhook, num embed vermelho com o nome **Valorant News**. Roda de graça no GitHub Actions a cada 15 minutos.

## Setup

1. **Webhook no Discord:** Configurações do canal → Integrações → Webhooks → Novo webhook → Copiar URL.
2. **Repositório:** crie um repo **público** no GitHub e suba estes arquivos (incluindo a pasta `.github`).
3. **Secret:** no repo, Settings → Secrets and variables → Actions → **New repository secret**
   - Nome: `DISCORD_WEBHOOK_URL`
   - Valor: a URL do webhook
4. *(Opcional)* Ícone: na mesma tela, aba **Variables** → `AVATAR_URL` com o link de uma imagem (png/jpg).
5. **Primeiro teste:** aba Actions → *Valorant News* → **Run workflow**.

Na primeira execução ele manda só a notícia mais recente de cada site e marca o resto como já visto, pra não inundar o canal. Depois disso, só notícias novas.

## Arquivos

| Arquivo | Função |
|---|---|
| `bot.py` | Lê as listagens, abre cada notícia nova e envia o embed |
| `sent.json` | IDs já enviados (criado e commitado automaticamente) |
| `.github/workflows/news.yml` | Agendamento a cada 15 min |

## Ajustes

- **Frequência:** altere o `cron` no `news.yml` (mínimo 5 min).
- **Cor:** `COLOR` no `bot.py`.
- **Limites:** variáveis `MAX_POSTS_PER_RUN` e `FIRST_RUN_POSTS`.

## Observações

- Repo **público** = minutos de Actions ilimitados. Em repo privado são 2.000 min/mês; a cada 15 min estoura — use `*/30` ou mais.
- O webhook fica no Secret, não aparece no código público.
- Os sites não têm API oficial; o bot lê o HTML. Se algum mudar de layout, o log do Actions mostra "nenhum link encontrado".
