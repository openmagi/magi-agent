export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.1"
  }
  public: {
    Tables: {
      analytics_daily: {
        Row: {
          cache_creation_tokens: number
          cache_read_tokens: number
          date: string
          email_credit_cents: number
          firecrawl_credit_cents: number
          input_tokens: number
          llm_credit_cents: number
          llm_request_count: number
          output_tokens: number
          search_credit_cents: number
          total_cost_cents: number
          updated_at: string
          user_id: string
          user_message_count: number
        }
        Insert: {
          cache_creation_tokens?: number
          cache_read_tokens?: number
          date: string
          email_credit_cents?: number
          firecrawl_credit_cents?: number
          input_tokens?: number
          llm_credit_cents?: number
          llm_request_count?: number
          output_tokens?: number
          search_credit_cents?: number
          total_cost_cents?: number
          updated_at?: string
          user_id: string
          user_message_count?: number
        }
        Update: {
          cache_creation_tokens?: number
          cache_read_tokens?: number
          date?: string
          email_credit_cents?: number
          firecrawl_credit_cents?: number
          input_tokens?: number
          llm_credit_cents?: number
          llm_request_count?: number
          output_tokens?: number
          search_credit_cents?: number
          total_cost_cents?: number
          updated_at?: string
          user_id?: string
          user_message_count?: number
        }
        Relationships: []
      }
      analytics_daily_by_model: {
        Row: {
          cache_creation_tokens: number
          cache_read_tokens: number
          cost_cents: number
          date: string
          input_tokens: number
          model: string
          output_tokens: number
          request_count: number
          updated_at: string
          user_id: string
        }
        Insert: {
          cache_creation_tokens?: number
          cache_read_tokens?: number
          cost_cents?: number
          date: string
          input_tokens?: number
          model: string
          output_tokens?: number
          request_count?: number
          updated_at?: string
          user_id: string
        }
        Update: {
          cache_creation_tokens?: number
          cache_read_tokens?: number
          cost_cents?: number
          date?: string
          input_tokens?: number
          model?: string
          output_tokens?: number
          request_count?: number
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      app_channel_messages: {
        Row: {
          bot_id: string
          channel_name: string
          content: string
          created_at: string | null
          id: string
          role: string
        }
        Insert: {
          bot_id: string
          channel_name: string
          content: string
          created_at?: string | null
          id?: string
          role?: string
        }
        Update: {
          bot_id?: string
          channel_name?: string
          content?: string
          created_at?: string | null
          id?: string
          role?: string
        }
        Relationships: [
          {
            foreignKeyName: "app_channel_messages_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      app_channels: {
        Row: {
          bot_id: string
          category: string | null
          created_at: string | null
          display_name: string | null
          id: string
          memory_mode: string
          model_selection: string | null
          name: string
          position: number | null
          router_type: string | null
        }
        Insert: {
          bot_id: string
          category?: string | null
          created_at?: string | null
          display_name?: string | null
          id?: string
          memory_mode?: string
          model_selection?: string | null
          name: string
          position?: number | null
          router_type?: string | null
        }
        Update: {
          bot_id?: string
          category?: string | null
          created_at?: string | null
          display_name?: string | null
          id?: string
          memory_mode?: string
          model_selection?: string | null
          name?: string
          position?: number | null
          router_type?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "app_channels_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_mission_artifacts: {
        Row: {
          created_at: string
          id: string
          kind: string
          metadata: Json
          mission_id: string
          preview: string | null
          run_id: string | null
          storage_key: string | null
          title: string
          uri: string | null
        }
        Insert: {
          created_at?: string
          id?: string
          kind: string
          metadata?: Json
          mission_id: string
          preview?: string | null
          run_id?: string | null
          storage_key?: string | null
          title: string
          uri?: string | null
        }
        Update: {
          created_at?: string
          id?: string
          kind?: string
          metadata?: Json
          mission_id?: string
          preview?: string | null
          run_id?: string | null
          storage_key?: string | null
          title?: string
          uri?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "agent_mission_artifacts_mission_id_fkey"
            columns: ["mission_id"]
            isOneToOne: false
            referencedRelation: "agent_missions"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_mission_artifacts_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "agent_mission_runs"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_mission_events: {
        Row: {
          actor_id: string | null
          actor_type: string
          created_at: string
          event_type: string
          id: string
          message: string | null
          mission_id: string
          payload: Json
          run_id: string | null
        }
        Insert: {
          actor_id?: string | null
          actor_type: string
          created_at?: string
          event_type: string
          id?: string
          message?: string | null
          mission_id: string
          payload?: Json
          run_id?: string | null
        }
        Update: {
          actor_id?: string | null
          actor_type?: string
          created_at?: string
          event_type?: string
          id?: string
          message?: string | null
          mission_id?: string
          payload?: Json
          run_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "agent_mission_events_mission_id_fkey"
            columns: ["mission_id"]
            isOneToOne: false
            referencedRelation: "agent_missions"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_mission_events_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "agent_mission_runs"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_mission_runs: {
        Row: {
          bot_id: string
          cron_id: string | null
          error_code: string | null
          error_message: string | null
          finished_at: string | null
          id: string
          metadata: Json
          mission_id: string
          result_preview: string | null
          session_key: string | null
          spawn_task_id: string | null
          started_at: string
          status: string
          stdout_preview: string | null
          trigger_type: string
          turn_id: string | null
        }
        Insert: {
          bot_id: string
          cron_id?: string | null
          error_code?: string | null
          error_message?: string | null
          finished_at?: string | null
          id?: string
          metadata?: Json
          mission_id: string
          result_preview?: string | null
          session_key?: string | null
          spawn_task_id?: string | null
          started_at?: string
          status?: string
          stdout_preview?: string | null
          trigger_type: string
          turn_id?: string | null
        }
        Update: {
          bot_id?: string
          cron_id?: string | null
          error_code?: string | null
          error_message?: string | null
          finished_at?: string | null
          id?: string
          metadata?: Json
          mission_id?: string
          result_preview?: string | null
          session_key?: string | null
          spawn_task_id?: string | null
          started_at?: string
          status?: string
          stdout_preview?: string | null
          trigger_type?: string
          turn_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "agent_mission_runs_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_mission_runs_mission_id_fkey"
            columns: ["mission_id"]
            isOneToOne: false
            referencedRelation: "agent_missions"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_missions: {
        Row: {
          assignee_bot_id: string | null
          assignee_profile: string | null
          bot_id: string
          budget_cents: number | null
          budget_turns: number | null
          channel_id: string
          channel_type: string
          claimed_by: string | null
          claimed_until: string | null
          completed_at: string | null
          created_at: string
          created_by: string
          id: string
          idempotency_key: string | null
          kind: string
          last_event_at: string | null
          metadata: Json
          org_id: string | null
          parent_mission_id: string | null
          priority: number
          root_mission_id: string | null
          status: string
          summary: string | null
          title: string
          updated_at: string
          used_cents: number
          used_turns: number
          user_id: string
        }
        Insert: {
          assignee_bot_id?: string | null
          assignee_profile?: string | null
          bot_id: string
          budget_cents?: number | null
          budget_turns?: number | null
          channel_id: string
          channel_type: string
          claimed_by?: string | null
          claimed_until?: string | null
          completed_at?: string | null
          created_at?: string
          created_by: string
          id?: string
          idempotency_key?: string | null
          kind: string
          last_event_at?: string | null
          metadata?: Json
          org_id?: string | null
          parent_mission_id?: string | null
          priority?: number
          root_mission_id?: string | null
          status?: string
          summary?: string | null
          title: string
          updated_at?: string
          used_cents?: number
          used_turns?: number
          user_id: string
        }
        Update: {
          assignee_bot_id?: string | null
          assignee_profile?: string | null
          bot_id?: string
          budget_cents?: number | null
          budget_turns?: number | null
          channel_id?: string
          channel_type?: string
          claimed_by?: string | null
          claimed_until?: string | null
          completed_at?: string | null
          created_at?: string
          created_by?: string
          id?: string
          idempotency_key?: string | null
          kind?: string
          last_event_at?: string | null
          metadata?: Json
          org_id?: string | null
          parent_mission_id?: string | null
          priority?: number
          root_mission_id?: string | null
          status?: string
          summary?: string | null
          title?: string
          updated_at?: string
          used_cents?: number
          used_turns?: number
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "agent_missions_assignee_bot_id_fkey"
            columns: ["assignee_bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_missions_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_missions_parent_mission_id_fkey"
            columns: ["parent_mission_id"]
            isOneToOne: false
            referencedRelation: "agent_missions"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_missions_root_mission_id_fkey"
            columns: ["root_mission_id"]
            isOneToOne: false
            referencedRelation: "agent_missions"
            referencedColumns: ["id"]
          },
        ]
      }
      bot_email_inboxes: {
        Row: {
          bot_id: string
          created_at: string
          display_name: string | null
          email_address: string
          enabled: boolean
          id: string
          inbox_id: string
          updated_at: string
          user_id: string
        }
        Insert: {
          bot_id: string
          created_at?: string
          display_name?: string | null
          email_address: string
          enabled?: boolean
          id?: string
          inbox_id: string
          updated_at?: string
          user_id: string
        }
        Update: {
          bot_id?: string
          created_at?: string
          display_name?: string | null
          email_address?: string
          enabled?: boolean
          id?: string
          inbox_id?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "bot_email_inboxes_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "bot_email_inboxes_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      bot_wallet_policies: {
        Row: {
          bot_id: string
          created_at: string | null
          id: string
          is_active: boolean | null
          name: string
          policy_json: Json
          policy_type: string
          privy_policy_id: string
          updated_at: string | null
        }
        Insert: {
          bot_id: string
          created_at?: string | null
          id?: string
          is_active?: boolean | null
          name: string
          policy_json: Json
          policy_type?: string
          privy_policy_id: string
          updated_at?: string | null
        }
        Update: {
          bot_id?: string
          created_at?: string | null
          id?: string
          is_active?: boolean | null
          name?: string
          policy_json?: Json
          policy_type?: string
          privy_policy_id?: string
          updated_at?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "bot_wallet_policies_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      bot_x402_inboxes: {
        Row: {
          bot_id: string
          created_at: string
          email_address: string
          id: string
          inbox_id: string
          user_id: string
          username: string | null
        }
        Insert: {
          bot_id: string
          created_at?: string
          email_address: string
          id?: string
          inbox_id: string
          user_id: string
          username?: string | null
        }
        Update: {
          bot_id?: string
          created_at?: string
          email_address?: string
          id?: string
          inbox_id?: string
          user_id?: string
          username?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "bot_x402_inboxes_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "bot_x402_inboxes_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      bot_x402_payments: {
        Row: {
          amount_usdc: string
          bot_id: string
          created_at: string | null
          id: string
          status: string
          target_url: string
          tx_hash: string
        }
        Insert: {
          amount_usdc: string
          bot_id: string
          created_at?: string | null
          id?: string
          status?: string
          target_url: string
          tx_hash: string
        }
        Update: {
          amount_usdc?: string
          bot_id?: string
          created_at?: string | null
          id?: string
          status?: string
          target_url?: string
          tx_hash?: string
        }
        Relationships: [
          {
            foreignKeyName: "bot_x402_payments_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      bots: {
        Row: {
          agent_endpoint_url: string | null
          agent_skill_md: string | null
          api_key_mode: string
          bot_purpose: string | null
          container_id: string | null
          agent_rules: string | null
          created_at: string
          deployed_version: string
          disabled_skills: Json | null
          discord_bot_token: string | null
          discord_bot_username: string | null
          error_message: string | null
          gateway_port: number | null
          health_status: string
          id: string
          kb_storage_used_bytes: number | null
          language: string
          last_bot_activity_at: string | null
          last_health_check: string | null
          last_user_message_at: string | null
          model_selection: string
          name: string
          node_host_port: number | null
          node_name: string | null
          org_id: string | null
          privy_wallet_address: string | null
          privy_wallet_chain: string | null
          privy_wallet_id: string | null
          provisioning_step: string | null
          purpose_category: string | null
          purpose_preset: string | null
          registry_agent_id: string | null
          registry_tx_hash: string | null
          router_type: string
          status: string
          storage_used_bytes: number | null
          telegram_bot_token: string | null
          telegram_bot_username: string | null
          telegram_owner_id: number | null
          telegram_user_handle: string | null
          updated_at: string
          user_id: string
        }
        Insert: {
          agent_endpoint_url?: string | null
          agent_skill_md?: string | null
          api_key_mode: string
          bot_purpose?: string | null
          agent_rules?: string | null
          container_id?: string | null
          created_at?: string
          deployed_version?: string
          disabled_skills?: Json | null
          discord_bot_token?: string | null
          discord_bot_username?: string | null
          error_message?: string | null
          gateway_port?: number | null
          health_status?: string
          id?: string
          kb_storage_used_bytes?: number | null
          language?: string
          last_bot_activity_at?: string | null
          last_health_check?: string | null
          last_user_message_at?: string | null
          model_selection: string
          name: string
          node_host_port?: number | null
          node_name?: string | null
          org_id?: string | null
          privy_wallet_address?: string | null
          privy_wallet_chain?: string | null
          privy_wallet_id?: string | null
          provisioning_step?: string | null
          purpose_category?: string | null
          purpose_preset?: string | null
          registry_agent_id?: string | null
          registry_tx_hash?: string | null
          router_type?: string
          status?: string
          storage_used_bytes?: number | null
          telegram_bot_token?: string | null
          telegram_bot_username?: string | null
          telegram_owner_id?: number | null
          telegram_user_handle?: string | null
          updated_at?: string
          user_id: string
        }
        Update: {
          agent_endpoint_url?: string | null
          agent_skill_md?: string | null
          api_key_mode?: string
          bot_purpose?: string | null
          agent_rules?: string | null
          container_id?: string | null
          created_at?: string
          deployed_version?: string
          disabled_skills?: Json | null
          discord_bot_token?: string | null
          discord_bot_username?: string | null
          error_message?: string | null
          gateway_port?: number | null
          health_status?: string
          id?: string
          kb_storage_used_bytes?: number | null
          language?: string
          last_bot_activity_at?: string | null
          last_health_check?: string | null
          last_user_message_at?: string | null
          model_selection?: string
          name?: string
          node_host_port?: number | null
          node_name?: string | null
          org_id?: string | null
          privy_wallet_address?: string | null
          privy_wallet_chain?: string | null
          privy_wallet_id?: string | null
          provisioning_step?: string | null
          purpose_category?: string | null
          purpose_preset?: string | null
          registry_agent_id?: string | null
          registry_tx_hash?: string | null
          router_type?: string
          status?: string
          storage_used_bytes?: number | null
          telegram_bot_token?: string | null
          telegram_bot_username?: string | null
          telegram_owner_id?: number | null
          telegram_user_handle?: string | null
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "bots_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "bots_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      chat_attachments: {
        Row: {
          bot_id: string
          channel_name: string
          created_at: string | null
          direction: string
          filename: string
          id: string
          metadata: Json | null
          mimetype: string
          size_bytes: number
          storage_path: string
          user_id: string | null
        }
        Insert: {
          bot_id: string
          channel_name: string
          created_at?: string | null
          direction: string
          filename: string
          id?: string
          metadata?: Json | null
          mimetype: string
          size_bytes: number
          storage_path: string
          user_id?: string | null
        }
        Update: {
          bot_id?: string
          channel_name?: string
          created_at?: string | null
          direction?: string
          filename?: string
          id?: string
          metadata?: Json | null
          mimetype?: string
          size_bytes?: number
          storage_path?: string
          user_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "chat_attachments_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      chat_exports: {
        Row: {
          bot_id: string
          channel_name: string
          created_at: string
          created_by: string
          id: string
          markdown: string
          messages_json: Json
          public_id: string
          revoked_at: string | null
          title: string
        }
        Insert: {
          bot_id: string
          channel_name: string
          created_at?: string
          created_by: string
          id?: string
          markdown: string
          messages_json: Json
          public_id: string
          revoked_at?: string | null
          title: string
        }
        Update: {
          bot_id?: string
          channel_name?: string
          created_at?: string
          created_by?: string
          id?: string
          markdown?: string
          messages_json?: Json
          public_id?: string
          revoked_at?: string | null
          title?: string
        }
        Relationships: [
          {
            foreignKeyName: "chat_exports_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "chat_exports_created_by_fkey"
            columns: ["created_by"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      chat_message_deletions: {
        Row: {
          bot_id: string
          channel_name: string
          client_msg_id: string | null
          deleted_at: string
          id: string
        }
        Insert: {
          bot_id: string
          channel_name: string
          client_msg_id?: string | null
          deleted_at?: string
          id?: string
        }
        Update: {
          bot_id?: string
          channel_name?: string
          client_msg_id?: string | null
          deleted_at?: string
          id?: string
        }
        Relationships: [
          {
            foreignKeyName: "chat_message_deletions_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      chat_messages: {
        Row: {
          bot_id: string
          channel_name: string
          client_msg_id: string | null
          created_at: string
          encrypted_content: string
          id: string
          iv: string
          role: string
        }
        Insert: {
          bot_id: string
          channel_name: string
          client_msg_id?: string | null
          created_at?: string
          encrypted_content: string
          id?: string
          iv: string
          role: string
        }
        Update: {
          bot_id?: string
          channel_name?: string
          client_msg_id?: string | null
          created_at?: string
          encrypted_content?: string
          id?: string
          iv?: string
          role?: string
        }
        Relationships: [
          {
            foreignKeyName: "chat_messages_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      chat_reset_counters: {
        Row: {
          bot_id: string
          channel_name: string
          reset_count: number
          updated_at: string
        }
        Insert: {
          bot_id: string
          channel_name: string
          reset_count?: number
          updated_at?: string
        }
        Update: {
          bot_id?: string
          channel_name?: string
          reset_count?: number
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "chat_reset_counters_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      consultation_artifacts: {
        Row: {
          bot_id: string
          created_at: string
          filename: string
          id: string
          job_id: string
          knowledge_document_id: string | null
          mime: string
          artifact_type: string
          storage_path: string
          user_id: string
        }
        Insert: {
          bot_id: string
          created_at?: string
          filename: string
          id?: string
          job_id: string
          knowledge_document_id?: string | null
          mime: string
          artifact_type: string
          storage_path: string
          user_id: string
        }
        Update: {
          bot_id?: string
          created_at?: string
          filename?: string
          id?: string
          job_id?: string
          knowledge_document_id?: string | null
          mime?: string
          artifact_type?: string
          storage_path?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "consultation_artifacts_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "consultation_artifacts_job_id_fkey"
            columns: ["job_id"]
            isOneToOne: false
            referencedRelation: "consultation_jobs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "consultation_artifacts_knowledge_document_id_fkey"
            columns: ["knowledge_document_id"]
            isOneToOne: false
            referencedRelation: "knowledge_documents"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "consultation_artifacts_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      consultation_jobs: {
        Row: {
          backend: string
          bot_id: string
          channel_name: string | null
          completed_at: string | null
          created_at: string
          credits_actual: number
          credits_estimated: number
          duration_seconds: number | null
          error_message: string | null
          estimated_duration_seconds: number | null
          hotwords: string[]
          id: string
          progress_message: string | null
          progress_pct: number
          retain_source_audio: boolean
          source_delete_after: string | null
          source_filename: string
          source_mime: string
          source_size_bytes: number
          source_storage_path: string
          status: string
          updated_at: string
          user_id: string
          vertical_hint: string
        }
        Insert: {
          backend?: string
          bot_id: string
          channel_name?: string | null
          completed_at?: string | null
          created_at?: string
          credits_actual?: number
          credits_estimated?: number
          duration_seconds?: number | null
          error_message?: string | null
          estimated_duration_seconds?: number | null
          hotwords?: string[]
          id?: string
          progress_message?: string | null
          progress_pct?: number
          retain_source_audio?: boolean
          source_delete_after?: string | null
          source_filename: string
          source_mime: string
          source_size_bytes?: number
          source_storage_path: string
          status?: string
          updated_at?: string
          user_id: string
          vertical_hint?: string
        }
        Update: {
          backend?: string
          bot_id?: string
          channel_name?: string | null
          completed_at?: string | null
          created_at?: string
          credits_actual?: number
          credits_estimated?: number
          duration_seconds?: number | null
          error_message?: string | null
          estimated_duration_seconds?: number | null
          hotwords?: string[]
          id?: string
          progress_message?: string | null
          progress_pct?: number
          retain_source_audio?: boolean
          source_delete_after?: string | null
          source_filename?: string
          source_mime?: string
          source_size_bytes?: number
          source_storage_path?: string
          status?: string
          updated_at?: string
          user_id?: string
          vertical_hint?: string
        }
        Relationships: [
          {
            foreignKeyName: "consultation_jobs_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "consultation_jobs_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      conversion_jobs: {
        Row: {
          bot_id: string
          completed_at: string | null
          created_at: string
          credits_actual: number | null
          credits_estimated: number | null
          error_message: string | null
          id: string
          progress_message: string | null
          progress_pct: number
          result_filename: string | null
          result_storage_path: string | null
          source_filename: string
          source_mime: string
          source_page_count: number | null
          source_storage_path: string
          status: string
          target_format: string
          updated_at: string
          user_id: string
        }
        Insert: {
          bot_id: string
          completed_at?: string | null
          created_at?: string
          credits_actual?: number | null
          credits_estimated?: number | null
          error_message?: string | null
          id?: string
          progress_message?: string | null
          progress_pct?: number
          result_filename?: string | null
          result_storage_path?: string | null
          source_filename: string
          source_mime: string
          source_page_count?: number | null
          source_storage_path: string
          status?: string
          target_format: string
          updated_at?: string
          user_id: string
        }
        Update: {
          bot_id?: string
          completed_at?: string | null
          created_at?: string
          credits_actual?: number | null
          credits_estimated?: number | null
          error_message?: string | null
          id?: string
          progress_message?: string | null
          progress_pct?: number
          result_filename?: string | null
          result_storage_path?: string | null
          source_filename?: string
          source_mime?: string
          source_page_count?: number | null
          source_storage_path?: string
          status?: string
          target_format?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "conversion_jobs_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "conversion_jobs_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      credit_grants: {
        Row: {
          granted_cents: number
          period_start: string
          updated_at: string
          used_cents: number
          user_id: string
        }
        Insert: {
          granted_cents?: number
          period_start?: string
          updated_at?: string
          used_cents?: number
          user_id: string
        }
        Update: {
          granted_cents?: number
          period_start?: string
          updated_at?: string
          used_cents?: number
          user_id?: string
        }
        Relationships: []
      }
      credit_transactions: {
        Row: {
          amount_cents: number
          bot_id: string | null
          created_at: string
          description: string | null
          id: string
          stripe_payment_id: string | null
          tx_hash: string | null
          type: string
          user_id: string
        }
        Insert: {
          amount_cents: number
          bot_id?: string | null
          created_at?: string
          description?: string | null
          id?: string
          stripe_payment_id?: string | null
          tx_hash?: string | null
          type: string
          user_id: string
        }
        Update: {
          amount_cents?: number
          bot_id?: string | null
          created_at?: string
          description?: string | null
          id?: string
          stripe_payment_id?: string | null
          tx_hash?: string | null
          type?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "credit_transactions_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "credit_transactions_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      credits: {
        Row: {
          balance_cents: number
          created_at: string
          id: string
          updated_at: string
          user_id: string
        }
        Insert: {
          balance_cents?: number
          created_at?: string
          id?: string
          updated_at?: string
          user_id: string
        }
        Update: {
          balance_cents?: number
          created_at?: string
          id?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "credits_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: true
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      e2ee_key_sync: {
        Row: {
          created_at: string
          signature: string
          user_id: string
        }
        Insert: {
          created_at?: string
          signature: string
          user_id: string
        }
        Update: {
          created_at?: string
          signature?: string
          user_id?: string
        }
        Relationships: []
      }
      email_quotas: {
        Row: {
          billing_period_start: string
          monthly_limit: number
          updated_at: string
          used_count: number
          user_id: string
        }
        Insert: {
          billing_period_start?: string
          monthly_limit?: number
          updated_at?: string
          used_count?: number
          user_id: string
        }
        Update: {
          billing_period_start?: string
          monthly_limit?: number
          updated_at?: string
          used_count?: number
          user_id?: string
        }
        Relationships: []
      }
      email_usage: {
        Row: {
          billing_period_start: string
          bot_id: string | null
          cost_cents: number
          created_at: string
          direction: string
          id: string
          recipient_email: string | null
          sender_email: string | null
          subject: string | null
          user_id: string
        }
        Insert: {
          billing_period_start?: string
          bot_id?: string | null
          cost_cents?: number
          created_at?: string
          direction: string
          id?: string
          recipient_email?: string | null
          sender_email?: string | null
          subject?: string | null
          user_id: string
        }
        Update: {
          billing_period_start?: string
          bot_id?: string | null
          cost_cents?: number
          created_at?: string
          direction?: string
          id?: string
          recipient_email?: string | null
          sender_email?: string | null
          subject?: string | null
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "email_usage_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      gateway_tokens: {
        Row: {
          bot_id: string
          created_at: string
          id: string
          is_active: boolean
          token: string
          user_id: string
        }
        Insert: {
          bot_id: string
          created_at?: string
          id?: string
          is_active?: boolean
          token: string
          user_id: string
        }
        Update: {
          bot_id?: string
          created_at?: string
          id?: string
          is_active?: boolean
          token?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "gateway_tokens_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "gateway_tokens_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      google_selected_files: {
        Row: {
          created_at: string | null
          file_id: string
          icon_url: string | null
          id: string
          mime_type: string
          name: string
          user_id: string
        }
        Insert: {
          created_at?: string | null
          file_id: string
          icon_url?: string | null
          id?: string
          mime_type: string
          name: string
          user_id: string
        }
        Update: {
          created_at?: string | null
          file_id?: string
          icon_url?: string | null
          id?: string
          mime_type?: string
          name?: string
          user_id?: string
        }
        Relationships: []
      }
      knowledge_collections: {
        Row: {
          bot_id: string
          created_at: string
          document_count: number
          id: string
          name: string
          org_id: string | null
          scope: string
          total_chunks: number
          updated_at: string
        }
        Insert: {
          bot_id: string
          created_at?: string
          document_count?: number
          id?: string
          name: string
          org_id?: string | null
          scope?: string
          total_chunks?: number
          updated_at?: string
        }
        Update: {
          bot_id?: string
          created_at?: string
          document_count?: number
          id?: string
          name?: string
          org_id?: string | null
          scope?: string
          total_chunks?: number
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "fk_knowledge_collections_bot"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "knowledge_collections_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      knowledge_documents: {
        Row: {
          bot_id: string | null
          chunk_count: number | null
          collection_id: string
          content_hash: string | null
          converted_size: number | null
          created_at: string
          error_message: string | null
          filename: string
          id: string
          object_key_converted: string | null
          object_key_original: string | null
          org_id: string | null
          original_size: number | null
          page_count: number | null
          parent_document_id: string | null
          path: string | null
          qmd_collection: string
          scope: string
          sort_order: number
          source_external_id: string | null
          source_last_edited_at: string | null
          source_parent_external_id: string | null
          source_provider: string | null
          source_url: string | null
          status: string
          storage_path: string | null
        }
        Insert: {
          bot_id?: string | null
          chunk_count?: number | null
          collection_id: string
          content_hash?: string | null
          converted_size?: number | null
          created_at?: string
          error_message?: string | null
          filename: string
          id?: string
          object_key_converted?: string | null
          object_key_original?: string | null
          org_id?: string | null
          original_size?: number | null
          page_count?: number | null
          parent_document_id?: string | null
          path?: string | null
          qmd_collection: string
          scope?: string
          sort_order?: number
          source_external_id?: string | null
          source_last_edited_at?: string | null
          source_parent_external_id?: string | null
          source_provider?: string | null
          source_url?: string | null
          status?: string
          storage_path?: string | null
        }
        Update: {
          bot_id?: string | null
          chunk_count?: number | null
          collection_id?: string
          content_hash?: string | null
          converted_size?: number | null
          created_at?: string
          error_message?: string | null
          filename?: string
          id?: string
          object_key_converted?: string | null
          object_key_original?: string | null
          org_id?: string | null
          original_size?: number | null
          page_count?: number | null
          parent_document_id?: string | null
          path?: string | null
          qmd_collection?: string
          scope?: string
          sort_order?: number
          source_external_id?: string | null
          source_last_edited_at?: string | null
          source_parent_external_id?: string | null
          source_provider?: string | null
          source_url?: string | null
          status?: string
          storage_path?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "fk_knowledge_documents_bot"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "knowledge_documents_collection_id_fkey"
            columns: ["collection_id"]
            isOneToOne: false
            referencedRelation: "knowledge_collections"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "knowledge_documents_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "knowledge_documents_parent_document_id_fkey"
            columns: ["parent_document_id"]
            isOneToOne: false
            referencedRelation: "knowledge_documents"
            referencedColumns: ["id"]
          },
        ]
      }
      learned_skills: {
        Row: {
          id: string
          bot_id: string
          user_id: string
          skill_name: string
          content: string
          usage_count: number
          success_count: number
          status: string
          created_at: string
          reviewed_at: string | null
        }
        Insert: {
          id?: string
          bot_id: string
          user_id: string
          skill_name: string
          content: string
          usage_count?: number
          success_count?: number
          status?: string
          created_at?: string
          reviewed_at?: string | null
        }
        Update: {
          id?: string
          bot_id?: string
          user_id?: string
          skill_name?: string
          content?: string
          usage_count?: number
          success_count?: number
          status?: string
          created_at?: string
          reviewed_at?: string | null
        }
        Relationships: []
      }
      notion_sync_runs: {
        Row: {
          collection_id: string
          completed_at: string | null
          errors: Json
          id: string
          org_id: string
          pages_seen: number
          pages_skipped: number
          pages_synced: number
          source_id: string | null
          started_at: string
          status: string
          triggered_by: string | null
        }
        Insert: {
          collection_id: string
          completed_at?: string | null
          errors?: Json
          id?: string
          org_id: string
          pages_seen?: number
          pages_skipped?: number
          pages_synced?: number
          source_id?: string | null
          started_at?: string
          status?: string
          triggered_by?: string | null
        }
        Update: {
          collection_id?: string
          completed_at?: string | null
          errors?: Json
          id?: string
          org_id?: string
          pages_seen?: number
          pages_skipped?: number
          pages_synced?: number
          source_id?: string | null
          started_at?: string
          status?: string
          triggered_by?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "notion_sync_runs_collection_id_fkey"
            columns: ["collection_id"]
            isOneToOne: false
            referencedRelation: "knowledge_collections"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "notion_sync_runs_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "notion_sync_runs_source_id_fkey"
            columns: ["source_id"]
            isOneToOne: false
            referencedRelation: "notion_sync_sources"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "notion_sync_runs_triggered_by_fkey"
            columns: ["triggered_by"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      notion_sync_sources: {
        Row: {
          collection_id: string
          connected_user_id: string
          created_at: string
          enabled: boolean
          error_message: string | null
          id: string
          last_synced_at: string | null
          next_sync_at: string | null
          org_id: string
          root_database_id: string | null
          root_page_id: string | null
          root_title: string
          schedule_cron: string
          status: string
          updated_at: string
        }
        Insert: {
          collection_id: string
          connected_user_id: string
          created_at?: string
          enabled?: boolean
          error_message?: string | null
          id?: string
          last_synced_at?: string | null
          next_sync_at?: string | null
          org_id: string
          root_database_id?: string | null
          root_page_id?: string | null
          root_title?: string
          schedule_cron?: string
          status?: string
          updated_at?: string
        }
        Update: {
          collection_id?: string
          connected_user_id?: string
          created_at?: string
          enabled?: boolean
          error_message?: string | null
          id?: string
          last_synced_at?: string | null
          next_sync_at?: string | null
          org_id?: string
          root_database_id?: string | null
          root_page_id?: string | null
          root_title?: string
          schedule_cron?: string
          status?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "notion_sync_sources_collection_id_fkey"
            columns: ["collection_id"]
            isOneToOne: false
            referencedRelation: "knowledge_collections"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "notion_sync_sources_connected_user_id_fkey"
            columns: ["connected_user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "notion_sync_sources_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      org_invites: {
        Row: {
          created_at: string
          email: string
          expires_at: string
          id: string
          invited_by: string
          org_id: string
          status: string
          token: string
        }
        Insert: {
          created_at?: string
          email: string
          expires_at?: string
          id?: string
          invited_by: string
          org_id: string
          status?: string
          token?: string
        }
        Update: {
          created_at?: string
          email?: string
          expires_at?: string
          id?: string
          invited_by?: string
          org_id?: string
          status?: string
          token?: string
        }
        Relationships: [
          {
            foreignKeyName: "org_invites_invited_by_fkey"
            columns: ["invited_by"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "org_invites_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      org_members: {
        Row: {
          joined_at: string
          org_id: string
          role: string
          user_id: string
        }
        Insert: {
          joined_at?: string
          org_id: string
          role?: string
          user_id: string
        }
        Update: {
          joined_at?: string
          org_id?: string
          role?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "org_members_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "org_members_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: true
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      org_summaries: {
        Row: {
          created_at: string
          credits_used: number
          generated_by: string
          id: string
          member_count: number
          org_id: string
          summary: string
        }
        Insert: {
          created_at?: string
          credits_used?: number
          generated_by: string
          id?: string
          member_count?: number
          org_id: string
          summary: string
        }
        Update: {
          created_at?: string
          credits_used?: number
          generated_by?: string
          id?: string
          member_count?: number
          org_id?: string
          summary?: string
        }
        Relationships: [
          {
            foreignKeyName: "org_summaries_generated_by_fkey"
            columns: ["generated_by"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "org_summaries_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      organizations: {
        Row: {
          bot_template: Json
          created_at: string
          credit_balance: number
          id: string
          name: string
          owner_id: string
          slug: string
          updated_at: string
        }
        Insert: {
          bot_template?: Json
          created_at?: string
          credit_balance?: number
          id?: string
          name: string
          owner_id: string
          slug: string
          updated_at?: string
        }
        Update: {
          bot_template?: Json
          created_at?: string
          credit_balance?: number
          id?: string
          name?: string
          owner_id?: string
          slug?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "organizations_owner_id_fkey"
            columns: ["owner_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      plan_switch_log: {
        Row: {
          created_at: string
          from_plan: string
          id: string
          proration_credit_cents: number | null
          switch_type: string
          to_plan: string
          user_id: string
          was_trialing: boolean | null
        }
        Insert: {
          created_at?: string
          from_plan: string
          id?: string
          proration_credit_cents?: number | null
          switch_type: string
          to_plan: string
          user_id: string
          was_trialing?: boolean | null
        }
        Update: {
          created_at?: string
          from_plan?: string
          id?: string
          proration_credit_cents?: number | null
          switch_type?: string
          to_plan?: string
          user_id?: string
          was_trialing?: boolean | null
        }
        Relationships: [
          {
            foreignKeyName: "plan_switch_log_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      platform_settings: {
        Row: {
          key: string
          updated_at: string | null
          value: string
        }
        Insert: {
          key: string
          updated_at?: string | null
          value: string
        }
        Update: {
          key?: string
          updated_at?: string | null
          value?: string
        }
        Relationships: []
      }
      profiles: {
        Row: {
          alpha_vantage_api_key: string | null
          anthropic_api_key: string | null
          brave_api_key: string | null
          codex_access_token: string | null
          codex_refresh_token: string | null
          codex_token_expires_at: string | null
          created_at: string
          custom_base_url: string | null
          dart_api_key: string | null
          deepl_api_key: string | null
          display_name: string | null
          elevenlabs_api_key: string | null
          finnhub_api_key: string | null
          firecrawl_api_key: string | null
          fireworks_api_key: string | null
          fmp_api_key: string | null
          fred_api_key: string | null
          gemini_api_key: string | null
          github_token: string | null
          google_ads_developer_token: string | null
          google_api_key: string | null
          groq_api_key: string | null
          id: string
          onboarding_completed: boolean
          openai_api_key: string | null
          payout_address: string | null
          semantic_scholar_api_key: string | null
          serper_api_key: string | null
          stripe_customer_id: string | null
          updated_at: string
          zapier_mcp_url: string | null
        }
        Insert: {
          alpha_vantage_api_key?: string | null
          anthropic_api_key?: string | null
          brave_api_key?: string | null
          codex_access_token?: string | null
          codex_refresh_token?: string | null
          codex_token_expires_at?: string | null
          created_at?: string
          custom_base_url?: string | null
          dart_api_key?: string | null
          deepl_api_key?: string | null
          display_name?: string | null
          elevenlabs_api_key?: string | null
          finnhub_api_key?: string | null
          firecrawl_api_key?: string | null
          fireworks_api_key?: string | null
          fmp_api_key?: string | null
          fred_api_key?: string | null
          gemini_api_key?: string | null
          github_token?: string | null
          google_ads_developer_token?: string | null
          google_api_key?: string | null
          groq_api_key?: string | null
          id: string
          onboarding_completed?: boolean
          openai_api_key?: string | null
          payout_address?: string | null
          semantic_scholar_api_key?: string | null
          serper_api_key?: string | null
          stripe_customer_id?: string | null
          updated_at?: string
          zapier_mcp_url?: string | null
        }
        Update: {
          alpha_vantage_api_key?: string | null
          anthropic_api_key?: string | null
          brave_api_key?: string | null
          codex_access_token?: string | null
          codex_refresh_token?: string | null
          codex_token_expires_at?: string | null
          created_at?: string
          custom_base_url?: string | null
          dart_api_key?: string | null
          deepl_api_key?: string | null
          display_name?: string | null
          elevenlabs_api_key?: string | null
          finnhub_api_key?: string | null
          firecrawl_api_key?: string | null
          fireworks_api_key?: string | null
          fmp_api_key?: string | null
          fred_api_key?: string | null
          gemini_api_key?: string | null
          github_token?: string | null
          google_ads_developer_token?: string | null
          google_api_key?: string | null
          groq_api_key?: string | null
          id?: string
          onboarding_completed?: boolean
          openai_api_key?: string | null
          payout_address?: string | null
          semantic_scholar_api_key?: string | null
          serper_api_key?: string | null
          stripe_customer_id?: string | null
          updated_at?: string
          zapier_mcp_url?: string | null
        }
        Relationships: []
      }
      referral_codes: {
        Row: {
          code: string
          created_at: string
          id: string
          is_custom: boolean
          user_id: string
        }
        Insert: {
          code: string
          created_at?: string
          id?: string
          is_custom?: boolean
          user_id: string
        }
        Update: {
          code?: string
          created_at?: string
          id?: string
          is_custom?: boolean
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "referral_codes_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: true
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      referral_earnings: {
        Row: {
          created_at: string
          earning_cents: number
          forfeited: boolean
          id: string
          period_month: string
          referee_id: string
          referrer_id: string
          settled: boolean
          source_amount_cents: number
          source_payment_id: string | null
          source_type: string
        }
        Insert: {
          created_at?: string
          earning_cents: number
          forfeited?: boolean
          id?: string
          period_month: string
          referee_id: string
          referrer_id: string
          settled?: boolean
          source_amount_cents: number
          source_payment_id?: string | null
          source_type: string
        }
        Update: {
          created_at?: string
          earning_cents?: number
          forfeited?: boolean
          id?: string
          period_month?: string
          referee_id?: string
          referrer_id?: string
          settled?: boolean
          source_amount_cents?: number
          source_payment_id?: string | null
          source_type?: string
        }
        Relationships: [
          {
            foreignKeyName: "referral_earnings_referee_id_fkey"
            columns: ["referee_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "referral_earnings_referrer_id_fkey"
            columns: ["referrer_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      referral_payouts: {
        Row: {
          amount_cents: number
          amount_usdc: string
          claimed_at: string
          completed_at: string | null
          destination_address: string
          id: string
          referrer_id: string
          status: string
          tx_hash: string | null
        }
        Insert: {
          amount_cents: number
          amount_usdc: string
          claimed_at?: string
          completed_at?: string | null
          destination_address: string
          id?: string
          referrer_id: string
          status?: string
          tx_hash?: string | null
        }
        Update: {
          amount_cents?: number
          amount_usdc?: string
          claimed_at?: string
          completed_at?: string | null
          destination_address?: string
          id?: string
          referrer_id?: string
          status?: string
          tx_hash?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "referral_payouts_referrer_id_fkey"
            columns: ["referrer_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      referrals: {
        Row: {
          created_at: string
          id: string
          referee_id: string
          referral_code_id: string
          referrer_id: string
          stripe_coupon_id: string | null
        }
        Insert: {
          created_at?: string
          id?: string
          referee_id: string
          referral_code_id: string
          referrer_id: string
          stripe_coupon_id?: string | null
        }
        Update: {
          created_at?: string
          id?: string
          referee_id?: string
          referral_code_id?: string
          referrer_id?: string
          stripe_coupon_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "referrals_referee_id_fkey"
            columns: ["referee_id"]
            isOneToOne: true
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "referrals_referral_code_id_fkey"
            columns: ["referral_code_id"]
            isOneToOne: false
            referencedRelation: "referral_codes"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "referrals_referrer_id_fkey"
            columns: ["referrer_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      search_quotas: {
        Row: {
          billing_period_start: string
          monthly_limit: number
          updated_at: string
          used_count: number
          user_id: string
        }
        Insert: {
          billing_period_start?: string
          monthly_limit?: number
          updated_at?: string
          used_count?: number
          user_id: string
        }
        Update: {
          billing_period_start?: string
          monthly_limit?: number
          updated_at?: string
          used_count?: number
          user_id?: string
        }
        Relationships: []
      }
      search_usage: {
        Row: {
          billing_period_start: string
          bot_id: string | null
          cost_cents: number
          created_at: string
          id: string
          query: string
          user_id: string
        }
        Insert: {
          billing_period_start?: string
          bot_id?: string | null
          cost_cents?: number
          created_at?: string
          id?: string
          query: string
          user_id: string
        }
        Update: {
          billing_period_start?: string
          bot_id?: string | null
          cost_cents?: number
          created_at?: string
          id?: string
          query?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "search_usage_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      service_usage_logs: {
        Row: {
          action: string
          bot_id: string
          created_at: string
          id: number
          latency_ms: number | null
          service: string
          success: boolean
          user_id: string
        }
        Insert: {
          action: string
          bot_id: string
          created_at?: string
          id?: never
          latency_ms?: number | null
          service: string
          success?: boolean
          user_id: string
        }
        Update: {
          action?: string
          bot_id?: string
          created_at?: string
          id?: never
          latency_ms?: number | null
          service?: string
          success?: boolean
          user_id?: string
        }
        Relationships: []
      }
      skill_analytics_daily: {
        Row: {
          avg_steps: number | null
          date: string
          fail_count: number
          partial_count: number
          skill_name: string
          success_count: number
          total_count: number
          unique_bots: number
          unique_users: number
          updated_at: string
        }
        Insert: {
          avg_steps?: number | null
          date: string
          fail_count?: number
          partial_count?: number
          skill_name: string
          success_count?: number
          total_count?: number
          unique_bots?: number
          unique_users?: number
          updated_at?: string
        }
        Update: {
          avg_steps?: number | null
          date?: string
          fail_count?: number
          partial_count?: number
          skill_name?: string
          success_count?: number
          total_count?: number
          unique_bots?: number
          unique_users?: number
          updated_at?: string
        }
        Relationships: []
      }
      skill_executions: {
        Row: {
          bot_id: string
          created_at: string
          error_message: string | null
          executed_at: string
          id: string
          ingested_at: string
          outcome: string
          skill_name: string
          step_count: number
          user_id: string
        }
        Insert: {
          bot_id: string
          created_at?: string
          error_message?: string | null
          executed_at: string
          id?: string
          ingested_at?: string
          outcome: string
          skill_name: string
          step_count?: number
          user_id: string
        }
        Update: {
          bot_id?: string
          created_at?: string
          error_message?: string | null
          executed_at?: string
          id?: string
          ingested_at?: string
          outcome?: string
          skill_name?: string
          step_count?: number
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "skill_executions_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      skill_refinement_suggestions: {
        Row: {
          id: string
          skill_name: string
          failure_rate: number
          sample_errors: string[]
          suggested_diff: string
          status: string
          created_at: string
        }
        Insert: {
          id?: string
          skill_name: string
          failure_rate: number
          sample_errors?: string[]
          suggested_diff: string
          status?: string
          created_at?: string
        }
        Update: {
          id?: string
          skill_name?: string
          failure_rate?: number
          sample_errors?: string[]
          suggested_diff?: string
          status?: string
          created_at?: string
        }
        Relationships: []
      }
      sub_agents_cache: {
        Row: {
          bot_id: string
          fetched_at: string
          registry_data: Json
        }
        Insert: {
          bot_id: string
          fetched_at?: string
          registry_data?: Json
        }
        Update: {
          bot_id?: string
          fetched_at?: string
          registry_data?: Json
        }
        Relationships: [
          {
            foreignKeyName: "sub_agents_cache_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: true
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
        ]
      }
      subscriptions: {
        Row: {
          billing_interval: string
          created_at: string
          current_period_end: string | null
          current_period_start: string | null
          id: string
          plan: string
          scheduled_change_at: string | null
          scheduled_plan: string | null
          status: string
          stripe_customer_id: string
          stripe_subscription_id: string | null
          trial_ends_at: string | null
          trial_started_at: string | null
          updated_at: string
          user_id: string
        }
        Insert: {
          billing_interval?: string
          created_at?: string
          current_period_end?: string | null
          current_period_start?: string | null
          id?: string
          plan: string
          scheduled_change_at?: string | null
          scheduled_plan?: string | null
          status: string
          stripe_customer_id: string
          stripe_subscription_id?: string | null
          trial_ends_at?: string | null
          trial_started_at?: string | null
          updated_at?: string
          user_id: string
        }
        Update: {
          billing_interval?: string
          created_at?: string
          current_period_end?: string | null
          current_period_start?: string | null
          id?: string
          plan?: string
          scheduled_change_at?: string | null
          scheduled_plan?: string | null
          status?: string
          stripe_customer_id?: string
          stripe_subscription_id?: string | null
          trial_ends_at?: string | null
          trial_started_at?: string | null
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "subscriptions_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: true
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      usage_logs: {
        Row: {
          api_key_mode: string
          bot_id: string
          cache_creation_tokens: number
          cache_read_tokens: number
          cost_cents: number
          created_at: string
          id: string
          input_tokens: number
          model: string
          org_id: string | null
          output_tokens: number
          trigger_type: string
          user_id: string
        }
        Insert: {
          api_key_mode: string
          bot_id: string
          cache_creation_tokens?: number
          cache_read_tokens?: number
          cost_cents: number
          created_at?: string
          id?: string
          input_tokens: number
          model: string
          org_id?: string | null
          output_tokens: number
          trigger_type?: string
          user_id: string
        }
        Update: {
          api_key_mode?: string
          bot_id?: string
          cache_creation_tokens?: number
          cache_read_tokens?: number
          cost_cents?: number
          created_at?: string
          id?: string
          input_tokens?: number
          model?: string
          org_id?: string | null
          output_tokens?: number
          trigger_type?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "usage_logs_bot_id_fkey"
            columns: ["bot_id"]
            isOneToOne: false
            referencedRelation: "bots"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "usage_logs_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "usage_logs_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      user_consents: {
        Row: {
          consent_type: string
          created_at: string
          id: string
          ip_address: string | null
          policy_version: string
          status: string
          user_agent: string | null
          user_id: string
        }
        Insert: {
          consent_type: string
          created_at?: string
          id?: string
          ip_address?: string | null
          policy_version: string
          status: string
          user_agent?: string | null
          user_id: string
        }
        Update: {
          consent_type?: string
          created_at?: string
          id?: string
          ip_address?: string | null
          policy_version?: string
          status?: string
          user_agent?: string | null
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "user_consents_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      user_device_data: {
        Row: {
          data: Json
          data_type: string
          id: string
          synced_at: string
          user_id: string
        }
        Insert: {
          data: Json
          data_type: string
          id?: string
          synced_at?: string
          user_id: string
        }
        Update: {
          data?: Json
          data_type?: string
          id?: string
          synced_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "user_device_data_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      user_device_permissions: {
        Row: {
          granted: boolean
          granted_at: string | null
          id: string
          permission: string
          user_id: string
        }
        Insert: {
          granted?: boolean
          granted_at?: string | null
          id?: string
          permission: string
          user_id: string
        }
        Update: {
          granted?: boolean
          granted_at?: string | null
          id?: string
          permission?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "user_device_permissions_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      user_integrations: {
        Row: {
          access_token: string | null
          created_at: string
          id: string
          metadata: Json
          provider: string
          refresh_token: string | null
          scopes: string[] | null
          status: string
          token_expires_at: string | null
          updated_at: string
          user_id: string
        }
        Insert: {
          access_token?: string | null
          created_at?: string
          id?: string
          metadata?: Json
          provider: string
          refresh_token?: string | null
          scopes?: string[] | null
          status?: string
          token_expires_at?: string | null
          updated_at?: string
          user_id: string
        }
        Update: {
          access_token?: string | null
          created_at?: string
          id?: string
          metadata?: Json
          provider?: string
          refresh_token?: string | null
          scopes?: string[] | null
          status?: string
          token_expires_at?: string | null
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "user_integrations_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      user_interactions: {
        Row: {
          bot_id: string
          channel: string
          created_at: string
          id: string
          user_id: string
        }
        Insert: {
          bot_id: string
          channel?: string
          created_at?: string
          id?: string
          user_id: string
        }
        Update: {
          bot_id?: string
          channel?: string
          created_at?: string
          id?: string
          user_id?: string
        }
        Relationships: []
      }
      wallet_usage_logs: {
        Row: {
          cache_creation_tokens: number
          cache_read_tokens: number
          cost_microcents: number
          created_at: string
          id: number
          input_tokens: number
          model: string
          output_tokens: number
          tier: string | null
          wallet_address: string
        }
        Insert: {
          cache_creation_tokens?: number
          cache_read_tokens?: number
          cost_microcents?: number
          created_at?: string
          id?: never
          input_tokens?: number
          model: string
          output_tokens?: number
          tier?: string | null
          wallet_address: string
        }
        Update: {
          cache_creation_tokens?: number
          cache_read_tokens?: number
          cost_microcents?: number
          created_at?: string
          id?: never
          input_tokens?: number
          model?: string
          output_tokens?: number
          tier?: string | null
          wallet_address?: string
        }
        Relationships: []
      }
      x402_accounts: {
        Row: {
          created_at: string
          credit_balance_microcents: number
          id: number
          is_blocked: boolean
          last_active_at: string
          total_topped_up_usdc: number
          wallet_address: string
        }
        Insert: {
          created_at?: string
          credit_balance_microcents?: number
          id?: never
          is_blocked?: boolean
          last_active_at?: string
          total_topped_up_usdc?: number
          wallet_address: string
        }
        Update: {
          created_at?: string
          credit_balance_microcents?: number
          id?: never
          is_blocked?: boolean
          last_active_at?: string
          total_topped_up_usdc?: number
          wallet_address?: string
        }
        Relationships: []
      }
      x402_topup_history: {
        Row: {
          amount_usdc: number
          created_at: string
          credit_microcents: number
          id: number
          payment_signature: string
          wallet_address: string
        }
        Insert: {
          amount_usdc: number
          created_at?: string
          credit_microcents: number
          id?: never
          payment_signature: string
          wallet_address: string
        }
        Update: {
          amount_usdc?: number
          created_at?: string
          credit_microcents?: number
          id?: never
          payment_signature?: string
          wallet_address?: string
        }
        Relationships: []
      }
      x402_usage_logs: {
        Row: {
          cost_microcents: number
          created_at: string
          endpoint: string
          id: number
          service: string
          upstream_cost_microcents: number
          wallet_address: string
        }
        Insert: {
          cost_microcents: number
          created_at?: string
          endpoint: string
          id?: never
          service: string
          upstream_cost_microcents?: number
          wallet_address: string
        }
        Update: {
          cost_microcents?: number
          created_at?: string
          endpoint?: string
          id?: never
          service?: string
          upstream_cost_microcents?: number
          wallet_address?: string
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      adjust_org_credits: {
        Args: { p_adjustment: number; p_org_id: string }
        Returns: undefined
      }
      check_and_deduct_credits: {
        Args: { p_amount: number; p_user_id: string }
        Returns: boolean
      }
      check_and_deduct_org_credits: {
        Args: { p_amount: number; p_org_id: string }
        Returns: boolean
      }
      claim_stripe_credit: {
        Args: {
          p_amount_cents: number
          p_description: string
          p_event_id: string | null
          p_event_type: string
          p_stripe_payment_id: string | null
          p_type: string
          p_user_id: string
        }
        Returns: boolean
      }
      claim_usdc_credit: {
        Args: {
          p_amount_cents: number
          p_from_address: string
          p_tx_hash: string
          p_user_id: string
        }
        Returns: boolean
      }
      check_and_use_email: {
        Args: {
          p_bot_id: string
          p_direction: string
          p_recipient: string
          p_sender: string
          p_subject: string
          p_user_id: string
        }
        Returns: string
      }
      check_and_use_search: {
        Args: { p_bot_id: string; p_query: string; p_user_id: string }
        Returns: string
      }
      claim_referral_payout: {
        Args: {
          p_daily_limit_cents?: number
          p_destination_address: string
          p_min_claim_cents?: number
          p_referrer_id: string
        }
        Returns: {
          amount_cents: number | null
          amount_usdc: string | null
          destination_address: string | null
          error_code: string | null
          payout_id: string | null
        }[]
      }
      deduct_credits_allow_negative: {
        Args: { p_amount: number; p_user_id: string }
        Returns: undefined
      }
      increment_collection_counts: {
        Args: {
          p_chunk_delta: number
          p_collection_id: string
          p_doc_delta: number
        }
        Returns: undefined
      }
      increment_credits: {
        Args: { p_amount: number; p_user_id: string }
        Returns: undefined
      }
      increment_kb_storage: {
        Args: { p_bot_id: string; p_bytes_delta: number }
        Returns: undefined
      }
      log_email_usage: {
        Args: {
          p_bot_id: string
          p_direction: string
          p_recipient: string
          p_sender: string
          p_subject: string
          p_user_id: string
        }
        Returns: undefined
      }
      reset_credit_grant: {
        Args: { p_granted_cents: number; p_user_id: string }
        Returns: number
      }
      reset_email_quota: {
        Args: { p_monthly_limit: number; p_user_id: string }
        Returns: undefined
      }
      reset_search_quota: {
        Args: { p_monthly_limit: number; p_user_id: string }
        Returns: undefined
      }
      track_grant_usage: {
        Args: { p_amount: number; p_user_id: string }
        Returns: undefined
      }
      upsert_skill_daily: {
        Args: {
          p_avg_steps: number
          p_date: string
          p_fail: number
          p_partial: number
          p_skill_name: string
          p_success: number
          p_total: number
          p_unique_bots: number
          p_unique_users: number
        }
        Returns: undefined
      }
      wallet_log_usage: {
        Args: {
          p_cache_creation: number
          p_cache_read: number
          p_cost_microcents: number
          p_input_tokens: number
          p_model: string
          p_output_tokens: number
          p_tier: string
          p_wallet: string
        }
        Returns: undefined
      }
      x402_adjust: {
        Args: { p_amount: number; p_wallet: string }
        Returns: undefined
      }
      x402_balance: { Args: { p_wallet: string }; Returns: number }
      x402_check_and_deduct: {
        Args: { p_amount: number; p_wallet: string }
        Returns: boolean
      }
      x402_refund: {
        Args: { p_amount: number; p_wallet: string }
        Returns: undefined
      }
      x402_topup: {
        Args: {
          p_amount_usdc: number
          p_credit_microcents: number
          p_signature: string
          p_wallet: string
        }
        Returns: number
      }
      x402_topup_by_tx: {
        Args: {
          p_chain_id: string
          p_credit_microcents: string
          p_log_index: number
          p_tx_hash: string
          p_wallet: string
          p_amount_usdc_raw: string
        }
        Returns: number
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const
