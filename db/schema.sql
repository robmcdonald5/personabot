\restrict dbmate

-- Dumped from database version 17.9
-- Dumped by pg_dump version 18.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: downloaded_media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.downloaded_media (
    media_id bigint NOT NULL,
    message_id bigint NOT NULL,
    guild_id bigint NOT NULL,
    original_url text NOT NULL,
    local_path text NOT NULL,
    content_type text,
    file_size bigint,
    downloaded_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: downloaded_media_media_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.downloaded_media ALTER COLUMN media_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.downloaded_media_media_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: guild_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_config (
    guild_id bigint NOT NULL,
    guild_name text NOT NULL,
    scrape_channels jsonb DEFAULT '[]'::jsonb NOT NULL,
    included_users jsonb DEFAULT '[]'::jsonb NOT NULL,
    scrape_start_date text,
    scrape_end_date text,
    default_limit integer DEFAULT 10000 NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    message_id bigint NOT NULL,
    guild_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    channel_name text DEFAULT ''::text NOT NULL,
    author_id bigint NOT NULL,
    author_name text NOT NULL,
    content text NOT NULL,
    "timestamp" text NOT NULL,
    reaction_count integer DEFAULT 0 NOT NULL,
    reply_to_id bigint,
    thread_id bigint,
    is_pinned boolean DEFAULT false NOT NULL,
    attachment_count integer DEFAULT 0 NOT NULL,
    embed_count integer DEFAULT 0 NOT NULL,
    word_count integer DEFAULT 0 NOT NULL,
    job_id text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: scrape_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scrape_jobs (
    job_id text NOT NULL,
    guild_id bigint NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    channels jsonb DEFAULT '[]'::jsonb NOT NULL,
    messages_found integer DEFAULT 0 NOT NULL,
    messages_stored integer DEFAULT 0 NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    error_message text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: downloaded_media downloaded_media_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.downloaded_media
    ADD CONSTRAINT downloaded_media_pkey PRIMARY KEY (media_id);


--
-- Name: guild_config guild_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_config
    ADD CONSTRAINT guild_config_pkey PRIMARY KEY (guild_id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (message_id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: scrape_jobs scrape_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scrape_jobs
    ADD CONSTRAINT scrape_jobs_pkey PRIMARY KEY (job_id);


--
-- Name: idx_media_downloaded_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_downloaded_at ON public.downloaded_media USING btree (downloaded_at);


--
-- Name: idx_media_guild; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_guild ON public.downloaded_media USING btree (guild_id);


--
-- Name: idx_media_message; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_message ON public.downloaded_media USING btree (message_id);


--
-- Name: idx_messages_channel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_channel ON public.messages USING btree (guild_id, channel_id, "timestamp");


--
-- Name: idx_messages_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_created_at ON public.messages USING btree (created_at);


--
-- Name: idx_messages_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_job ON public.messages USING btree (guild_id, job_id) WHERE (job_id IS NOT NULL);


--
-- Name: idx_messages_reply; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_reply ON public.messages USING btree (reply_to_id) WHERE (reply_to_id IS NOT NULL);


--
-- Name: idx_messages_user_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_user_time ON public.messages USING btree (guild_id, author_id, "timestamp");


--
-- Name: idx_scrape_jobs_guild_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_scrape_jobs_guild_status ON public.scrape_jobs USING btree (guild_id, status, created_at DESC);


--
-- Name: downloaded_media downloaded_media_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.downloaded_media
    ADD CONSTRAINT downloaded_media_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(message_id) ON DELETE CASCADE;


--
-- Name: scrape_jobs scrape_jobs_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scrape_jobs
    ADD CONSTRAINT scrape_jobs_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guild_config(guild_id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict dbmate


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20260413000001');
