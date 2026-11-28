# Покриття вимог практичного заняття № 1

Фінальний набір містить 61 тестовий випадок (259 у попередній версії).
Скорочено дублювання між етапами та надлишкові варіанти перевірок. Збережено
покриття вимог методичних вказівок, але не заявляється тотожне покриття всіх
попередніх граничних і регресійних випадків. Правила блокчейну не змінювалися.

Запуск із PZ1: `python -m pytest -q`.

| Файл | Кількість |
|---|---:|
| test_step1.py | 5 |
| test_step2.py | 7 |
| test_step3.py | 5 |
| test_step4.py | 6 |
| test_step5.py | 13 |
| test_step6.py | 8 |
| test_step6_review.py | 6 |
| test_step7.py | 7 |
| test_step8.py | 4 |
| **Разом** | **61** |

## Відповідність вимогам

| Вимога методички | Збережена перевірка / доказ |
|---|---|
| Ключі, адреса, Ed25519 | test_step1: test_wallet_address_and_valid_signature, test_signed_fields_and_key_tampering |
| Детермінована серіалізація транзакції та заголовка | test_step1: test_deterministic_serialization_and_signature; test_step3: test_header_fields_hash_and_canonical_serialization |
| Мемпул: підпис | test_step2: test_signature_tamper_rejected |
| Мемпул: баланс і резерв комісій | test_step2: test_balance_reserves_amount_and_fee_without_confirmation, test_unconfirmed_incoming_cannot_be_spent |
| Мемпул: nonce та дублікати | test_step2: test_nonce_is_contiguous_and_per_sender, test_duplicate_in_mempool_rejected, test_confirmation_updates_state_and_prevents_replay |
| Поля блока, SHA-256 | test_step3: test_header_fields_hash_and_canonical_serialization |
| Парний і непарний корінь Меркла | test_step3: test_two_leaves_merkle_root_manual, test_three_leaves_duplicate_last_at_odd_level (3 та 5 листків), test_empty_and_single_leaf_roots |
| PoW зі складністю | test_step4: test_mining_finds_valid_nonce_and_counts_attempts; test_step5: test_invalid_pow, test_wrong_difficulty_even_with_pow; test_step6: test_shorter_chain_with_more_work_wins |
| Винагорода з комісіями | test_step4: test_coinbase_reward_and_fee_structure; test_step5: test_confirm_and_pay_reward, test_invalid_coinbase_even_with_valid_pow |
| Коригування складності через K блоків | test_step4: test_retarget_only_at_interval, test_retarget_fast_slow_and_thresholds, test_retarget_stays_within_min_and_max_limits; test_step6: test_shorter_chain_with_more_work_wins |
| Генезис, додавання, баланси | test_step5: test_genesis_is_identical_and_altered_history_rejected, test_confirm_and_pay_reward |
| Збереження та відновлення | test_step5: test_save_and_load_roundtrip, test_storage_detects_tampered_hash_and_transaction; demo_step5.py (окремий запуск процесу) |
| Повна перевірка ланцюга | test_step5: test_wrong_parent_or_height, test_wrong_difficulty_even_with_pow, test_invalid_pow, test_future_and_nonincreasing_timestamps, test_invalid_signature_and_merkle, test_duplicate_body_rejected_even_if_merkle_ambiguous, test_block_nonce_and_confirmed_replay, test_partial_state_rolled_back_on_second_transfer, test_invalid_coinbase_even_with_valid_pow; test_step8: test_five_required_attacks |
| Три окремі вузли, поширення транзакцій та блоків | test_step6: test_three_process_network_propagation_and_fork (запускає demo_step6.py) |
| Вибір за сумарною роботою | test_step6: test_shorter_chain_with_more_work_wins, test_equal_or_lower_work_keeps_local, test_invalid_more_work_rejected, test_three_process_network_propagation_and_fork |
| Повернення транзакцій при реорганізації | test_step6: test_reorg_recovers_orphan_and_pending_nonce_not_coinbase, test_conflicting_orphan_is_rejected, test_confirmed_on_winning_branch_not_requeued, test_orphan_with_lost_funding_cannot_return, test_three_process_network_propagation_and_fork |
| Вісім команд CLI | test_step7: test_create_and_read_encrypted_wallet_cli (wallet-create), test_cli_mine_send_chain_balance_validate (mine/send/chain/balance/validate), test_sync_and_dynamic_peer_cli (peer-add/sync) |
| Атака 1: зміна суми старої транзакції | test_step8: test_five_required_attacks (атака 1) |
| Атака 2: перерахунок блока | test_step8: test_five_required_attacks (атака 2) |
| Атака 3: підроблений підпис | test_step8: test_five_required_attacks (атака 3) |
| Атака 4: подвійна витрата | test_step8: test_five_required_attacks (атака 4) |
| Атака 5: повтор і nonce | test_step8: test_five_required_attacks (атака 5) |
| Не менше 12 тестів | 61 тест; python -m pytest -q |
| Чотири складності, ≥10 запусків на кожну | benchmark_step8.py; measurements/step8_benchmark_runs.csv; test_step8: test_benchmark_summary_and_four_difficulty_graph |
| Графік середнього із розкидом та логарифмічною віссю часу | benchmark_step8.py, функція plot; той самий тест перевіряє зведення та експорт PNG; графік і числове пояснення у звіті |
| README із запуском трьох вузлів | README.md: python demo_step6.py; автоматизований тест трьох процесів |
| Додаткове: легкий клієнт, доказ Меркла | test_step8: test_merkle_membership_and_light_client |
| Додаткове: атака 51 %, графік | test_step8: test_controlled_majority_fork_and_model; simulate_majority_step8.py, measurements/step8_majority.csv, графік у звіті |

Бенчмарк цілеспрямовано відокремлений від регулярних автоматизованих перевірок.
Тест читає фактичні збережені дані експерименту, перевіряє кількість вимірювань,
статистику та побудову графіка, а не повторює дорогі 40 запусків PoW.
